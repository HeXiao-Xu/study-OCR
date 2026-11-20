"""局部二次识别（S3-3）：提交侧校验 + worker 侧执行。

契约是**异步** `POST /invoices/{id}/reocr`（API.md §2）：提交只建作业并入队，
真正的裁剪识别在 worker 里做。写回原则：**只更新 raw_text/confidence/source=ocr_retry，
绝不改 field_value**——值由人工在 S3-6 选择（D-060）。
"""
from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, RoleForbiddenError, ValidationFailedError
from app.models.enums import ExtractStatus, FieldSource, InvoiceStatus, OcrStage, OcrStatus
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.ocr_lines import OcrLine
from app.models.users import User
from app.ocr import refine
from app.ocr.detector import OcrOptions, get_engine
from app.ocr.intake import PreparedPage, prepare_pages
from app.schemas.invoice import ReocrData, ReocrRequest
from app.services import job_service
from app.services.confidence import FieldConfidence, aggregate_invoice, level_for
from app.services.repository import get_visible_or_404
from app.services.storage import StorageService, get_storage

logger = logging.getLogger("app.reocr")

_REOCR_STAGE = OcrStage.RECOGNIZE.value
_ACTIVE = (OcrStatus.PENDING.value, OcrStatus.RUNNING.value)
TEMP_PREFIX = "reocr-"
# 重识别出的文本低于该分数视为"没识别到字"（非文本区域），记 skipped 而不是写回脏值
MIN_TEXT_SCORE = 0.3


@dataclass
class ReocrDeps:
    """可注入依赖：默认走真实实现，测试传假对象（不加载模型/不连 MinIO）。"""

    engine: object | None = None
    storage: object | None = None
    prepare_pages: Callable[..., list[PreparedPage]] | None = None
    clock: Callable[[], float] = time.monotonic


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

def _load_page_images(
    file: InvoiceFile, fields: Sequence[str], rows: dict[str, InvoiceField], *,
    settings: Settings, storage: StorageService, prepare: Callable[..., list[PreparedPage]],
) -> dict[int, PreparedPage]:
    pages_needed = max(
        (rows[name].source_page_no or 1) for name in fields if name in rows
    ) if fields else 1
    source = storage.get_bytes(
        file.storage_key, max_bytes=settings.max_upload_mb * 1024 * 1024
    )
    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
        path = Path(tmp) / "source.bin"
        path.write_bytes(source)
        prepared = prepare(path, max_pages=pages_needed, pdf_dpi=250)
    return {page.page_no: page for page in prepared}


def run_reocr(db: Session, job: OcrJob, *, deps: ReocrDeps | None = None) -> None:
    """worker 侧执行：逐字段裁剪重识别，只改 raw_text/confidence，最后重算整单分。"""
    deps = deps or ReocrDeps()
    settings = get_settings()
    fields = list((job.params_json or {}).get("fields") or [])
    started = deps.clock()
    deadline = started + settings.reread_budget_seconds

    invoice = db.get(Invoice, job.invoice_id)
    if invoice is None or invoice.status != InvoiceStatus.DRAFT.value:
        raise ValidationFailedError("票据不存在或已不在草稿态，重识别取消")
    file = db.get(InvoiceFile, invoice.file_id)
    if file is None:
        raise ValidationFailedError("票据文件记录不存在")

    rows = {
        row.field_name: row
        for row in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)
        ).all()
    }

    engine = deps.engine or get_engine(OcrOptions.from_settings(settings))
    storage = deps.storage or get_storage()
    prepare = deps.prepare_pages or prepare_pages
    page_by_no = _load_page_images(
        file, fields, rows, settings=settings, storage=storage, prepare=prepare
    )

    results: list[dict[str, Any]] = []
    applied = skipped = 0
    for name in fields:
        row = rows.get(name)
        if row is None:
            results.append({"field_name": name, "reason": "missing_field"})
            skipped += 1
            continue
        if deps.clock() > deadline:
            results.append({"field_name": name, "reason": "budget"})
            skipped += 1
            continue

        page_no = row.source_page_no or 1
        line = db.scalar(
            select(OcrLine).where(
                OcrLine.invoice_id == invoice.id,
                OcrLine.page_no == page_no,
                OcrLine.line_no == row.source_line_no,
            )
        )
        page = page_by_no.get(page_no)
        if line is None or not line.box_json or page is None:
            results.append({"field_name": name, "reason": "missing_line"})
            skipped += 1
            continue

        old_confidence = float(row.confidence or 0)
        candidate = refine.reread_box(
            page.image,
            line.box_json,
            engine=engine,
            factor=settings.reread_scale,
            pad_min_px=settings.reread_pad_min_px,
            pad_ratio=settings.reread_pad_ratio,
        )
        if not candidate.text.strip() or candidate.score < MIN_TEXT_SCORE:
            results.append({
                "field_name": name,
                "reason": "no_text",
                "new_text": candidate.text,
                "new_score": candidate.score,
                "bbox": list(candidate.bbox),
            })
            skipped += 1
            continue

        did_apply = candidate.score > old_confidence
        if did_apply:
            # 只改"机器看到了什么"与置信度；值必须由人工选（D-060）
            row.raw_text = candidate.text
            row.confidence = Decimal(str(round(candidate.score, 3)))
            row.source = FieldSource.OCR_RETRY.value
            row.need_review = level_for(candidate.score) != "auto"
            row.updated_at = datetime.now(timezone.utc)
            applied += 1
        else:
            skipped += 1
        results.append({
            "field_name": name,
            "old_value": row.field_value,
            "old_confidence": old_confidence,
            "new_text": candidate.text,
            "new_score": candidate.score,
            "applied": did_apply,
            "reason": None if did_apply else "not_improved",
            "bbox": list(candidate.bbox),
        })

    # 整单分与抽取状态重算：以库内 confidence 为准（重识别阶段没有 missing_required 信息）
    all_rows = db.scalars(
        select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)
    ).all()
    levels = [
        FieldConfidence(row.field_name, float(row.confidence or 0),
                        level_for(float(row.confidence or 0)))
        for row in all_rows
        if row.confidence is not None
    ]
    aggregate = aggregate_invoice(levels)
    invoice.overall_confidence = Decimal(str(aggregate.overall))
    invoice.extract_status = (
        ExtractStatus.NEED_CONFIRM.value if aggregate.needs_review
        else ExtractStatus.RAW.value
    )

    job.metrics = {
        "kind": "reocr",
        "budget_ms": int(settings.reread_budget_seconds * 1000),
        "elapsed_ms": int((deps.clock() - started) * 1000),
        "applied": applied,
        "skipped": skipped,
        "fields": results,
        "overall_confidence": aggregate.overall,
    }
