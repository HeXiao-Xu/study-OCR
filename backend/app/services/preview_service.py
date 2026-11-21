"""临时预览（S5-4）：验签通过后把明文放进带 TTL 的临时对象，供财务短时查看。

三条硬约束：

1. **数据库不落明文**：解密结果只写 MinIO 的 `previews/{token}/...` 对象；
2. **凭据即能力**：`verify_token`（30min）→ `preview_token`（30min，可重复读取）；
3. **TTL 清理任务**：`cleanup_expired_previews` 按对象最后修改时间删除过期对象
   （Redis 凭据自己 TTL 过期），由 APScheduler 周期执行。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import AppError, ConflictError, NotFoundError, ServiceUnavailableError
from app.core.logging import get_trace_id
from app.models.envelopes import Envelope
from app.models.report_forms import ReportForm
from app.models.users import User
from app.schemas.finance import DecryptData
from app.services.audit import append_audit
from app.services.credential_store import CredentialStore, preview_key, verify_key
from app.services.key_service import load_finance_private_key, provider_for
from app.services.report_crypto import EnvelopeOpenError, open_sealed_steps
from app.services.image_service import render_pdf_page
from app.services.repository import get_visible_or_404
from app.services.storage import StorageService
from app.services.submit_service import load_items

logger = logging.getLogger("app.finance.preview")

_REPORT_OBJECT = "report.json"


class PreviewExpiredError(AppError):
    """预览凭据过期（API.md §10：410/41001）。"""

    code = 41001
    http_status = 410
    message = "预览凭据已过期"


def decrypt_report(
    db: Session,
    user: User,
    report_id: int,
    verify_token: str,
    *,
    settings: Settings,
    store: CredentialStore,
    storage: StorageService,
) -> DecryptData:
    form = get_visible_or_404(db, ReportForm, report_id, user)

    stored = store.get(verify_key(form.id))
    if stored is None or stored != verify_token:
        raise ConflictError(
            "验签凭据无效或已过期", detail={"reason_code": "verify_token_invalid"}
        )

    envelope = _form_envelope(db, form.id)
    items = load_items(db, form.id)
    file_envelopes = _file_envelopes(db, [row.file.id for row in items])
    if envelope is None or not envelope.ciphertext_b64:
        raise ConflictError("单据信封缺失", detail={"reason_code": "bad_envelope"})

    provider = provider_for(settings)
    finance_key = load_finance_private_key(db, envelope.recipient_cert_id, settings)

    # 1) 单据 JSON
    try:
        form_opened = open_sealed_steps(
            provider=provider,
            private_key=finance_key,
            key_algo=envelope.key_algo,
            cipher_algo=envelope.cipher_algo,
            recipient_cert_fp=_cert_fp(db, envelope.recipient_cert_id),
            wrapped_key_b64=envelope.wrapped_key_b64,
            iv_b64=envelope.iv_b64,
            mac_b64=envelope.mac_b64,
            ciphertext=base64.b64decode(envelope.ciphertext_b64, validate=True),
            payload_sha256=envelope.payload_sha256,
        )
    except EnvelopeOpenError as exc:
        raise ConflictError(
            "信封拆封失败", detail={"reason_code": exc.reason}
        ) from exc
    report_bytes = form_opened.plaintext

    # 2) 影像明文 → 临时对象
    token = secrets.token_urlsafe(24)
    prefix = f"{settings.preview_object_prefix}/{token}"
    written: list[str] = []
    files_meta: dict[str, str] = {}
    try:
        _put(storage, f"{prefix}/{_REPORT_OBJECT}", report_bytes, "application/json", written)
        for row in items:
            file_envelope = file_envelopes.get(row.file.id)
            if file_envelope is None or not file_envelope.ciphertext_storage_key:
                raise ConflictError(
                    f"票据 {row.invoice.id} 缺少文件级信封",
                    detail={"reason_code": "bad_envelope"},
                )
            try:
                opened = open_sealed_steps(
                    provider=provider,
                    private_key=finance_key,
                    key_algo=file_envelope.key_algo,
                    cipher_algo=file_envelope.cipher_algo,
                    recipient_cert_fp=_cert_fp(db, file_envelope.recipient_cert_id),
                    wrapped_key_b64=file_envelope.wrapped_key_b64,
                    iv_b64=file_envelope.iv_b64,
                    mac_b64=file_envelope.mac_b64,
                    ciphertext=storage.get_bytes(
                        file_envelope.ciphertext_storage_key,
                        max_bytes=settings.max_upload_mb * 1024 * 1024,
                    ),
                    payload_sha256=file_envelope.payload_sha256,
                )
            except EnvelopeOpenError as exc:
                raise ConflictError(
                    "信封拆封失败", detail={"reason_code": exc.reason}
                ) from exc
            object_name = f"{prefix}/files/{row.file.id}.bin"
            _put(storage, object_name, opened.plaintext, row.file.mime, written)
            files_meta[str(row.file.id)] = row.file.mime
    except Exception:
        # 半成品预览对象：尽力清理，避免明文留下（原始异常继续上抛）
        for key in written:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "预览对象回滚清理失败",
                    extra={"event": "preview_cleanup_failed", "report_id": form.id},
                )
        raise

    now = datetime.now(timezone.utc)
    ttl = settings.preview_token_ttl_seconds
    meta = {
        "report_id": form.id,
        "created_at": now.isoformat(),
        "files": files_meta,
    }
    try:
        store.put(preview_key(token), json.dumps(meta, ensure_ascii=False), ttl)
    except Exception as exc:  # noqa: BLE001
        for key in written:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001
                pass
        raise ServiceUnavailableError("凭据存储不可用，请重试") from exc

    append_audit(
        db,
        action="decrypt",
        trace_id=get_trace_id(),
        actor_id=user.id,
        actor_role=user.role,
        target_type="form",
        target_id=form.id,
        detail={
            "report_no": form.report_no,
            "result": "ok",
            "file_count": len(items),
            "preview_ttl_seconds": ttl,
        },
    )
    db.commit()

    logger.info(
        "财务解密预览签发",
        extra={"event": "finance_decrypt_ok", "report_id": form.id, "user_id": user.id},
    )
    return DecryptData(
        preview_token=token,
        expires_at=now + timedelta(seconds=ttl),
        ttl_seconds=ttl,
        content_type="application/json",
        size=len(report_bytes),
        fields_preview=json.loads(report_bytes),
    )


def read_preview(
    store: CredentialStore, storage: StorageService, token: str, *, settings: Settings
) -> tuple[bytes, dict[str, Any]]:
    meta = _read_meta(store, token)
    content = storage.get_bytes(
        f"{settings.preview_object_prefix}/{token}/{_REPORT_OBJECT}",
        max_bytes=settings.max_upload_mb * 1024 * 1024,
    )
    return content, meta


def read_preview_file(
    store: CredentialStore,
    storage: StorageService,
    token: str,
    file_id: int,
    *,
    settings: Settings,
    page: int = 1,
) -> tuple[bytes, str]:
    """读预览影像；PDF 与票据详情一致，按 250DPI 渲染成 PNG 再返回（浏览器 <img> 不能直接显示 PDF）。"""
    meta = _read_meta(store, token)
    mime = (meta.get("files") or {}).get(str(file_id))
    if mime is None:
        raise NotFoundError("预览文件不存在")
    content = storage.get_bytes(
        f"{settings.preview_object_prefix}/{token}/files/{file_id}.bin",
        max_bytes=settings.max_upload_mb * 1024 * 1024,
    )
    if mime == "application/pdf":
        try:
            return render_pdf_page(content, page), "image/png"
        except Exception as exc:  # noqa: BLE001 - 渲染失败按预览不可用处理
            raise ConflictError("PDF 页面渲染失败") from exc
    return content, mime


def cleanup_expired_previews(
    storage: StorageService, settings: Settings, *, now: datetime | None = None
) -> int:
    """删除 `previews/` 下最后修改时间早于 TTL 的对象；返回删除数量。"""
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(seconds=settings.preview_token_ttl_seconds)
    deleted = 0
    for key, modified in storage.list_objects(f"{settings.preview_object_prefix}/"):
        if modified is None:
            continue
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        if modified >= cutoff:
            continue
        try:
            storage.delete(key)
            deleted += 1
        except Exception:  # noqa: BLE001 - 单个对象删失败不阻塞整轮清理
            logger.warning(
                "过期预览对象删除失败",
                extra={"event": "preview_cleanup_delete_failed"},
            )
    if deleted:
        logger.info(
            "过期预览对象已清理",
            extra={"event": "preview_cleanup", "deleted": deleted},
        )
    return deleted


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _read_meta(store: CredentialStore, token: str) -> dict[str, Any]:
    raw = store.get(preview_key(token))
    if raw is None:
        raise PreviewExpiredError()
    try:
        meta = json.loads(raw)
    except ValueError as exc:
        raise PreviewExpiredError() from exc
    return meta if isinstance(meta, dict) else {}


def _put(
    storage: StorageService,
    key: str,
    content: bytes,
    content_type: str,
    written: list[str],
) -> None:
    storage.put(key, io.BytesIO(content), len(content), content_type)
    written.append(key)


def _form_envelope(db: Session, form_id: int) -> Envelope | None:
    return db.scalars(select(Envelope).where(Envelope.form_id == form_id).limit(1)).first()


def _file_envelopes(db: Session, file_ids: list[int]) -> dict[int, Envelope]:
    if not file_ids:
        return {}
    rows = db.scalars(select(Envelope).where(Envelope.file_id.in_(file_ids))).all()
    return {row.file_id: row for row in rows if row.file_id is not None}


def _cert_fp(db: Session, cert_id: int) -> str:
    from app.models.certificates import Certificate

    fingerprint = db.scalar(
        select(Certificate.fingerprint_sha256).where(Certificate.id == cert_id)
    )
    return fingerprint or ""