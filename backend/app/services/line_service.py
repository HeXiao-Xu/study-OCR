"""行框数据（S3-5）：`ocr_lines` 四点（校正图）→ 原图外接框；**不返回行文本**。

为什么服务端做映射：字段 bbox 自 S3-3 起就是原图坐标，前端只允许一种坐标系；
行框若直接给校正图坐标，照片类票据在放大/透视后会整体偏移。
PDF 没有预处理（D-035），映射为恒等；照片重跑一次 `prepare_input` 拿 `to_original`。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_lines import OcrLine
from app.models.users import User
from app.ocr.intake import prepare_input
from app.ocr.refine import bbox_to_original, points_to_bbox
from app.schemas.invoice import InvoiceLinesData, OcrLineBoxOut
from app.services.repository import get_visible_or_404
from app.services.storage import StorageService

__all__ = ["get_invoice_lines"]

_PLAINTEXT_ERROR = "加密影像解密尚未接入（S4）"


def _valid_box(box: object) -> list[list[float]] | None:
    if not isinstance(box, list) or len(box) != 4:
        return None
    points: list[list[float]] = []
    for point in box:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        points.append([float(point[0]), float(point[1])])
    return points


def get_invoice_lines(
    db: Session,
    user: User,
    invoice_id: int,
    *,
    page: int,
    settings: Settings,
    storage: StorageService,
) -> InvoiceLinesData:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    file_row = db.get(InvoiceFile, invoice.file_id)
    if file_row is None:
        raise NotFoundError("票据文件不存在")

    page_count = file_row.page_count or 1
    if page < 1 or page > page_count:
        raise ValidationFailedError(f"页码越界：1–{page_count}")
    if file_row.is_cipher or not settings.storage_allow_plaintext:
        raise ConflictError(_PLAINTEXT_ERROR)

    # PDF 不预处理 → 恒等映射；照片需要 to_original 才能把四点映射回原图
    matrix = None
    if file_row.mime != "application/pdf":
        data = storage.get_bytes(
            file_row.storage_key, max_bytes=settings.max_upload_mb * 1024 * 1024
        )
        prepared = prepare_input(data)
        matrix = prepared.preprocess.to_original if prepared.preprocess is not None else None

    rows = db.scalars(
        select(OcrLine)
        .where(OcrLine.invoice_id == invoice.id, OcrLine.page_no == page)
        .order_by(OcrLine.line_no.asc())
    ).all()

    owners = {
        (field.source_page_no or 1, field.source_line_no): field.field_name
        for field in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)
        ).all()
        if field.source_line_no is not None
    }

    lines: list[OcrLineBoxOut] = []
    for row in rows:
        points = _valid_box(row.box_json)
        if points is None:
            continue                      # 畸形框直接跳过，不制造 [0,0,0,0] 假框
        bbox = bbox_to_original(points_to_bbox(points), matrix)
        lines.append(
            OcrLineBoxOut(
                line_no=row.line_no,
                bbox=list(bbox),
                score=None if row.score is None else float(row.score),
                field_name=owners.get((page, row.line_no)),
            )
        )
    return InvoiceLinesData(invoice_id=invoice.id, page_no=page, lines=lines)
