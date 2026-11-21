"""票据影像渲染（S3-5）：原图直出 / PDF 按 250 DPI 渲染 / 预处理变体。

安全边界（D-063）：只有 `STORAGE_ALLOW_PLAINTEXT=true` 且文件 `is_cipher=false` 才返回明文；
S4 接入信封后在这里换成"内存解密 → 渲染 → 响应结束释放"，接口形状不变。
PDF 固定 **250 DPI** 渲染：与 S3-1 `prepare_pages(pdf_dpi=250)` 一致，否则字段框会按比例全偏。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import cv2
import numpy as np

from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.ocr.intake import prepare_input
from app.services.repository import get_visible_or_404
from app.services.storage import StorageService

__all__ = ["PDF_DPI", "RenderedImage", "get_invoice_image", "render_image", "render_pdf_page"]

PDF_DPI = 250
_VARIANTS = ("original", "preprocessed")
_PLAINTEXT_ERROR = "加密影像解密尚未接入（S4）"


@dataclass(frozen=True)
class RenderedImage:
    content: bytes
    media_type: str
    etag: str


def _etag(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _encode_png(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise ValidationFailedError("影像编码失败")
    return buffer.tobytes()


def render_pdf_page(data: bytes, page: int = 1, dpi: int = PDF_DPI) -> bytes:
    """PDF 指定页渲染成 PNG（S5-4 预览复用；与识别口径 250DPI 一致）。"""
    return _render_pdf_page(data, page, dpi)


def _render_pdf_page(data: bytes, page: int, dpi: int = PDF_DPI) -> bytes:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - 项目锁定了 PyMuPDF
        import fitz as pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        pixmap = document.load_page(page - 1).get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")


def render_image(
    file_row: InvoiceFile, data: bytes, *, page: int, variant: str, settings: Settings
) -> RenderedImage:
    """文件级纯渲染（不算权限、不下载）；HTTP 入口走 `get_invoice_image`。"""
    if file_row.is_cipher or not settings.storage_allow_plaintext:
        raise ConflictError(_PLAINTEXT_ERROR)
    if variant not in _VARIANTS:
        raise ValidationFailedError("variant 只能是 original / preprocessed")
    page_count = file_row.page_count or 1
    if page < 1 or page > page_count:
        raise ValidationFailedError(f"页码越界：1–{page_count}")

    if file_row.mime == "application/pdf":
        content = _render_pdf_page(data, page)
        return RenderedImage(content=content, media_type="image/png", etag=_etag(content))

    if variant == "preprocessed":
        prepared = prepare_input(data)
        content = _encode_png(prepared.image)
        return RenderedImage(content=content, media_type="image/png", etag=_etag(content))

    media_type = (
        file_row.mime
        if file_row.mime in {"image/jpeg", "image/png"}
        else "application/octet-stream"
    )
    return RenderedImage(content=data, media_type=media_type, etag=_etag(data))


def get_invoice_image(
    db,
    user,
    invoice_id: int,
    *,
    page: int,
    variant: str,
    settings: Settings,
    storage: StorageService,
) -> RenderedImage:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    file_row = db.get(InvoiceFile, invoice.file_id)
    if file_row is None:
        raise NotFoundError("票据文件不存在")
    # 先做闸门判定再下载：密文时没必要把字节读进内存
    if file_row.is_cipher or not settings.storage_allow_plaintext:
        raise ConflictError(_PLAINTEXT_ERROR)
    data = storage.get_bytes(
        file_row.storage_key, max_bytes=settings.max_upload_mb * 1024 * 1024
    )
    return render_image(file_row, data, page=page, variant=variant, settings=settings)