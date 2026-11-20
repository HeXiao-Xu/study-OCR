"""批量上传与排队（S3-8）：建单复用单文件上传；单用户并发 ≤N，其余排队由 after_job_end 补位。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import (
    AppError,
    ConflictError,
    DuplicateInvoiceError,
    NotFoundError,
    ValidationFailedError,
)
from app.models.enums import OcrStatus, UserRole
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.schemas.invoice import (
    BatchInvoiceItemOut,
    BatchSummaryData,
)
from app.services import job_service, ocr_status_service, upload_service
from app.services.storage import StorageService

__all__ = [
    "MAX_BATCH_FILES",
    "BatchCreated",
    "BatchItemResult",
    "batch_summary",
    "create_batch",
    "dispatch_for_job",
    "dispatch_pending",
    "retry_item",
]

MAX_BATCH_FILES = 30
_ACTIVE = (OcrStatus.PENDING.value, OcrStatus.RUNNING.value)


@dataclass(frozen=True)
class BatchItemResult:
    filename: str
    status: str
    invoice_id: int | None = None
    job_id: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class BatchCreated:
    batch_id: UUID
    items: list[BatchItemResult]
    created: int
    duplicates: int
    rejected: int


def create_batch(
    db: Session,
    user: User,
    uploads: list[UploadFile],
    *,
    settings: Settings,
    storage: StorageService,
) -> BatchCreated:
    if not 1 <= len(uploads) <= MAX_BATCH_FILES:
        raise ValidationFailedError(f"一次批量上传 1–{MAX_BATCH_FILES} 个文件")
    batch_id = uuid4()
    items: list[BatchItemResult] = []
    created = duplicates = rejected = 0
    for upload in uploads:
        filename = upload.filename or "-"
        try:
            data = upload_service.upload_invoice(
                db, user, upload, settings=settings, storage=storage
            )
        except DuplicateInvoiceError as exc:
            duplicates += 1
            items.append(
                BatchItemResult(
                    filename,
                    "duplicate",
                    invoice_id=exc.detail.get("invoice_id"),
                    error="影像已上传",
                )
            )
            continue
        except AppError as exc:
            # 单个文件失败不拖垮整批（DB/程序错误仍应抛出）
            rejected += 1
            items.append(BatchItemResult(filename, "rejected", error=exc.message))
            continue
        invoice = db.get(Invoice, data.invoice_id)
        job = db.get(OcrJob, data.job_id)
        if invoice is None or job is None:            # pragma: no cover - 刚建的行
            raise NotFoundError("批量上传后票据或作业丢失")
        invoice.batch_id = batch_id
        job.dispatched = False                        # 排队，由 dispatch_pending 统一入队
        db.commit()
        created += 1
        items.append(
            BatchItemResult(filename, "created", invoice_id=invoice.id, job_id=job.id)
        )
    return BatchCreated(batch_id, items, created, duplicates, rejected)


async def dispatch_pending(
    db: Session, uploader_id: int, *, queue, settings: Settings
) -> int:
    """把该用户排队的作业补到并发上限；返回本次投递数。"""
    active = (
        db.scalar(
            select(func.count())
            .select_from(OcrJob)
            .join(Invoice, Invoice.id == OcrJob.invoice_id)
            .where(
                Invoice.uploader_id == uploader_id,
                OcrJob.dispatched.is_(True),
                OcrJob.status.in_(_ACTIVE),
            )
        )
        or 0
    )
    slots = settings.ocr_max_active_per_user - active
    if slots <= 0:
        return 0
    jobs = db.scalars(
        select(OcrJob)
        .join(Invoice, Invoice.id == OcrJob.invoice_id)
        .where(
            Invoice.uploader_id == uploader_id,
            OcrJob.dispatched.is_(False),
            OcrJob.status == OcrStatus.PENDING.value,
        )
        .order_by(OcrJob.id)
        .limit(slots)
    ).all()
    moved = 0
    for job in jobs:
        job.dispatched = True
        db.commit()
        try:
            await queue.enqueue_ocr(job.id)
        except Exception:  # noqa: BLE001 - 队列不可用时如实置 failed，不假装成功
            await asyncio.to_thread(job_service.mark_enqueue_failed, job.id, db=db)
        moved += 1
    return moved


async def dispatch_for_job(job_id: int, *, queue, settings: Settings) -> int:
    """worker `after_job_end`：为刚结束作业所属用户补位。"""
    from app.core.database import get_session_factory

    db = get_session_factory()()
    try:
        job = db.get(OcrJob, job_id)
        if job is None:
            return 0
        invoice = db.get(Invoice, job.invoice_id)
        if invoice is None:
            return 0
        return await dispatch_pending(
            db, invoice.uploader_id, queue=queue, settings=settings
        )
    finally:
        db.close()


def batch_summary(db: Session, user: User, batch_id: UUID) -> BatchSummaryData:
    stmt = select(Invoice).where(Invoice.batch_id == batch_id)
    if user.role != UserRole.REVIEWER.value:
        stmt = stmt.where(Invoice.uploader_id == user.id)
    invoices = db.scalars(stmt.order_by(Invoice.id)).all()
    if not invoices:
        raise NotFoundError("批次不存在")

    counts = {"pending": 0, "running": 0, "succeeded": 0, "failed": 0}
    items: list[BatchInvoiceItemOut] = []
    for invoice in invoices:
        file_row = db.get(InvoiceFile, invoice.file_id)
        job = db.scalar(
            select(OcrJob)
            .where(OcrJob.invoice_id == invoice.id)
            .order_by(OcrJob.id.desc())
            .limit(1)
        )
        metrics = (job.metrics or {}) if job is not None else {}
        stages = ocr_status_service.build_stages(job, metrics)
        progress = ocr_status_service.compute_progress(
            [stage.model_dump() for stage in stages], invoice.ocr_status
        )
        counts[invoice.ocr_status] = counts.get(invoice.ocr_status, 0) + 1
        items.append(
            BatchInvoiceItemOut(
                invoice_id=invoice.id,
                filename=file_row.filename if file_row is not None else "-",
                ocr_status=invoice.ocr_status,
                extract_status=invoice.extract_status,
                progress=progress,
                error=job.error if job is not None else None,
            )
        )
    return BatchSummaryData(
        batch_id=batch_id,
        total=len(items),
        counts=counts,
        items=items,
        created_at=invoices[0].created_at,
    )


def retry_item(
    db: Session, user: User, batch_id: UUID, invoice_id: int
) -> tuple[int, int]:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.batch_id != batch_id or invoice.uploader_id != user.id:
        raise NotFoundError("批次项不存在")
    active = (
        db.scalar(
            select(func.count())
            .select_from(OcrJob)
            .where(OcrJob.invoice_id == invoice.id, OcrJob.status.in_(_ACTIVE))
        )
        or 0
    )
    if active:
        raise ConflictError("该票据已有进行中的识别任务")
    job = job_service.create_job(
        db, invoice_id=invoice.id, status=OcrStatus.PENDING.value
    )
    job.dispatched = False
    invoice.ocr_status = OcrStatus.PENDING.value
    db.commit()
    return invoice.id, job.id