"""上传业务（API.md §2 `POST /invoices/upload`）：校验 → 去重 → 存对象 → 落库 → 补偿。

顺序是定死的（任务卡坑③）：**先算 sha256 与去重，再写 MinIO**，否则重复影像会堆积对象。
落库与写对象不在同一事务里，所以 `storage.put` 之后的任何失败都必须补偿删除对象。
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import (
    DuplicateInvoiceError,
    NotFoundError,
    PayloadTooLargeError,
    RoleForbiddenError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.models.enums import InvoiceStatus, UserRole
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob  # noqa: F401 - 仅用于返回类型标注
from app.models.users import User
from app.schemas.invoice import UploadData, UploadDedup
from app.services import job_service
from app.services.storage import StorageService

logger = logging.getLogger("app.upload")

_CHUNK = 1024 * 1024
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PDF_MAGIC = b"%PDF-"
_IMAGE_MIMES = {"image/jpeg", "image/png"}
_PDF_MIME = "application/pdf"


@dataclass(frozen=True)
class ScannedUpload:
    size: int
    sha256: str
    mime: str
    page_count: int


def upload_invoice(
    db: Session,
    user: User,
    upload: UploadFile,
    *,
    settings: Settings,
    storage: StorageService,
) -> UploadData:
    # 权限矩阵：上传仅 submitter；reviewer 403；admin 404（不暴露业务资源）
    if user.role == UserRole.ADMIN.value:
        raise NotFoundError()
    if user.role != UserRole.SUBMITTER.value:
        raise RoleForbiddenError("只有提交人可以上传票据")

    scanned = _scan_and_validate(
        upload, settings.max_upload_mb * 1024 * 1024, settings.upload_max_pdf_pages
    )

    # 去重必须在写 MinIO 之前
    existing = db.scalar(select(InvoiceFile).where(InvoiceFile.sha256 == scanned.sha256))
    if existing is not None:
        raise _duplicate_error(db, existing)

    if not settings.storage_allow_plaintext:
        # 安全闸门：S4 的 SM4 信封未接入时，绝不静默落明文
        raise ServiceUnavailableError("加密存储尚未实现，当前配置禁止明文上传")

    logger.warning(
        "S4 前本地调试：票据影像明文落 MinIO（is_cipher=false）",
        extra={"event": "storage_plaintext", "user_id": user.id},
    )
    storage_key = storage.build_object_key()
    stream = upload.file
    stream.seek(0)
    storage.put(storage_key, stream, scanned.size, scanned.mime)

    try:
        file_row, invoice, job = _persist(
            db,
            uploader_id=user.id,
            filename=_safe_filename(upload.filename),
            scanned=scanned,
            storage_key=storage_key,
        )
    except IntegrityError:
        # 并发同 sha：唯一索引兜底；删掉刚写的对象，回查既有票据转 409
        db.rollback()
        _compensate(storage, storage_key)
        existing = db.scalar(
            select(InvoiceFile).where(InvoiceFile.sha256 == scanned.sha256)
        )
        if existing is not None:
            raise _duplicate_error(db, existing) from None
        raise
    except Exception:
        db.rollback()
        _compensate(storage, storage_key)
        raise

    return UploadData(
        invoice_id=invoice.id,
        file_id=file_row.id,
        job_id=job.id,
        sha256=scanned.sha256,
        status=invoice.status,
        ocr_status=invoice.ocr_status,
        dedup=UploadDedup(hit=False, invoice_id=None),
    )


# --------------------------------------------------------------------------- #
# 校验（不整文件进内存：分块哈希 + spooled 文件复用）
# --------------------------------------------------------------------------- #
def _scan_and_validate(upload: UploadFile, max_bytes: int, max_pdf_pages: int) -> ScannedUpload:
    stream = upload.file
    stream.seek(0)

    digest = hashlib.sha256()
    size = 0
    head = b""
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise PayloadTooLargeError(f"文件超过 {max_bytes // (1024 * 1024)}MB 上限")
        if len(head) < len(_PNG_MAGIC):
            head += chunk[: len(_PNG_MAGIC) - len(head)]
        digest.update(chunk)
    stream.seek(0)

    if size == 0:
        raise ValidationFailedError("空文件")

    mime = _detect_mime(head)
    if mime is None:
        raise ValidationFailedError("只支持 JPEG / PNG / PDF（按魔数判定）")

    if mime == _PDF_MIME:
        page_count = _pdf_page_count(stream, max_pdf_pages)
    else:
        _validate_image(stream)
        page_count = 1

    return ScannedUpload(
        size=size, sha256=digest.hexdigest(), mime=mime, page_count=page_count
    )


def _detect_mime(head: bytes) -> str | None:
    if head.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if head.startswith(_PNG_MAGIC):
        return "image/png"
    if head.startswith(_PDF_MAGIC):
        return _PDF_MIME
    return None


def _validate_image(stream) -> None:
    """Pillow 真解码一次：改扩展名的文本/可执行文件在这里被拒。"""
    try:
        with Image.open(stream) as image:
            image.verify()
    except Exception as exc:  # noqa: BLE001 - PIL 的异常类型不稳定，统一转业务错误
        raise ValidationFailedError("文件不是有效图片") from exc
    finally:
        stream.seek(0)


def _pdf_page_count(stream, max_pages: int) -> int:
    try:
        reader = PdfReader(stream)
        pages = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf 对损坏文件抛多种异常
        raise ValidationFailedError("PDF 无法解析") from exc
    finally:
        stream.seek(0)

    if pages == 0:
        raise ValidationFailedError("PDF 没有页面")
    if pages > max_pages:
        raise ValidationFailedError(f"PDF 最多 {max_pages} 页")
    return pages


# --------------------------------------------------------------------------- #
# 落库与补偿
# --------------------------------------------------------------------------- #
def _persist(
    db: Session,
    *,
    uploader_id: int,
    filename: str,
    scanned: ScannedUpload,
    storage_key: str,
) -> tuple[InvoiceFile, Invoice, OcrJob]:
    """同一事务写 file + invoice + ocr_jobs；调用方负责失败补偿。"""
    file_row = InvoiceFile(
        uploader_id=uploader_id,
        filename=filename,
        mime=scanned.mime,
        size=scanned.size,
        sha256=scanned.sha256,
        storage_key=storage_key,
        is_cipher=False,  # S4 接入信封后改为 True（D-044）
        page_count=scanned.page_count,
    )
    db.add(file_row)
    db.flush()

    invoice = Invoice(
        uploader_id=uploader_id,
        file_id=file_row.id,
        invoice_type="unknown",
        status=InvoiceStatus.DRAFT.value,
        ocr_status="pending",
        extract_status="raw",
    )
    db.add(invoice)
    db.flush()

    job = job_service.create_job(db, invoice_id=invoice.id)
    db.commit()
    for row in (file_row, invoice, job):
        db.refresh(row)
    return file_row, invoice, job


def _safe_filename(name: str | None) -> str:
    """原文件名只用于展示：去掉目录部分与控制字符，避免路径穿越/重名/中文路径问题。"""
    base = Path(name or "upload").name
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return (cleaned or "upload")[:255]


def _duplicate_error(db: Session, file_row: InvoiceFile) -> DuplicateInvoiceError:
    invoice = db.scalar(select(Invoice).where(Invoice.file_id == file_row.id))
    return DuplicateInvoiceError(
        "该影像已上传", detail={"invoice_id": invoice.id if invoice is not None else None}
    )


def _compensate(storage: StorageService, key: str) -> None:
    try:
        storage.delete(key)
    except Exception:  # noqa: BLE001 - 补偿失败只告警，不掩盖原始异常
        logger.exception(
            "补偿删除对象失败", extra={"event": "storage_compensation_failed"}
        )