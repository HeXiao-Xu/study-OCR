"""局部二次识别（S3-3）：提交侧校验 + worker 侧执行。

契约是**异步** `POST /invoices/{id}/reocr`（API.md §2）：提交只建作业并入队，
真正的裁剪识别在 worker 里做。写回原则：**只更新 raw_text/confidence/source=ocr_retry，
绝不改 field_value**——值由人工在 S3-6 选择（D-060）。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, RoleForbiddenError, ValidationFailedError
from app.models.enums import ExtractStatus, FieldSource, InvoiceStatus, OcrStage, OcrStatus
from app.models.invoice_fields import InvoiceField
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.schemas.invoice import ReocrData, ReocrRequest
from app.services import job_service
from app.services.repository import get_visible_or_404

logger = logging.getLogger("app.reocr")

_REOCR_STAGE = OcrStage.RECOGNIZE.value
_ACTIVE = (OcrStatus.PENDING.value, OcrStatus.RUNNING.value)


def _in_progress_job(db: Session, invoice_id: int) -> int | None:
    return db.scalar(
        select(OcrJob.id)
        .where(
            OcrJob.invoice_id == invoice_id,
            OcrJob.stage == _REOCR_STAGE,
            OcrJob.status.in_(_ACTIVE),
            OcrJob.params_json["kind"].astext == "reocr",
        )
        .limit(1)
    )


def submit_reocr(
    db: Session, user: User, invoice_id: int, payload: ReocrRequest
) -> ReocrData:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    if invoice.uploader_id != user.id:
        # 契约 §2 只写"本人"；reviewer 触发权限与 S3-6 一起定（遗留）
        raise RoleForbiddenError("只有票据本人可以触发重识别")

    if invoice.status != InvoiceStatus.DRAFT.value or invoice.extract_status not in (
        ExtractStatus.RAW.value,
        ExtractStatus.NEED_CONFIRM.value,
    ):
        raise ConflictError(
            f"当前状态（{invoice.status}/{invoice.extract_status}）不允许重识别"
        )
    if payload.version is not None and payload.version != invoice.version:
        raise ConflictError(f"版本冲突：当前 version={invoice.version}")

    rows = {
        row.field_name: row
        for row in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)
        ).all()
    }
    problems: list[str] = []
    for name in payload.fields:
        row = rows.get(name)
        if row is None:
            problems.append(f"{name}：字段不存在")
        elif row.source == FieldSource.MANUAL.value or row.confirmed:
            problems.append(f"{name}：人工值不可重识别")
        elif row.source == FieldSource.OCR_RETRY.value:
            problems.append(f"{name}：已重识别过")
        elif row.bbox_json is None or row.source_line_no is None:
            problems.append(f"{name}：缺少定位信息")
    if problems:
        raise ValidationFailedError("；".join(problems))

    if _in_progress_job(db, invoice.id) is not None:
        raise ConflictError("该票据已有进行中的重识别")

    job = job_service.create_job(db, invoice_id=invoice.id, stage=_REOCR_STAGE)
    job.params_json = {"kind": "reocr", "fields": list(payload.fields)}
    db.commit()
    db.refresh(job)
    logger.info(
        "重识别已受理",
        extra={"event": "reocr_submitted", "invoice_id": invoice.id, "task_id": job.id},
    )
    return ReocrData(invoice_id=invoice.id, job_id=job.id, fields=list(payload.fields))