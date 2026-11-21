"""财务验签（S5-4）：固定七步、失败归类、签名结论落库、审计、签发 `verify_token`。

顺序（API.md §5，写死不可换）：

```
envelope_lookup → mac → unwrap_cek → decrypt → cert_check → signature → digest_recheck
```

两条关键设计：

1. **摘要必须从库内原始数据重算**：`digest_recheck` 用 `report_forms.canonical_json` 里的 nonce
   + 当前库里的单据/明细重建签名域，与库里那份**逐字节**比较；只比 `signatures.digest_hex`
   （提交时存下的值）等于没验签（任务卡的坑）。
2. **失败不返回明文**：解密只在内存里发生，检查完即释放；`mac_mismatch`/`bad_padding` 只进审计与响应，
   **不写** `signatures.verify_result`（SCHEMA §9 约定）；签名层失败（`digest_mismatch`/`bad_signature`/`cert_invalid`）
   才把 active 签名改成失败，财务列表随即可见。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import AppError, ServiceUnavailableError
from app.core.logging import get_trace_id
from app.crypto.key_loader import (
    KeyLoadError,
    certificate_fingerprint,
    certificate_problem,
    recipient_key_material,
)
from app.crypto.sm.sm3 import sm3_digest
from app.models.certificates import Certificate
from app.models.envelopes import Envelope
from app.models.report_forms import ReportForm
from app.models.signatures import Signature
from app.models.users import User
from app.schemas.finance import VerifyData, VerifyStep
from app.services.audit import append_audit
from app.services.credential_store import CredentialStore, verify_key
from app.services.key_service import load_finance_private_key, provider_for
from app.services.report_crypto import (
    EnvelopeOpenError,
    algo_names,
    open_sealed_steps,
    rebuild_signing_canonical,
)
from app.services.repository import get_visible_or_404
from app.services.submit_service import load_items

logger = logging.getLogger("app.finance.verify")

STEP_ORDER: tuple[str, ...] = (
    "envelope_lookup",
    "mac",
    "unwrap_cek",
    "decrypt",
    "cert_check",
    "signature",
    "digest_recheck",
)

#: 只有这几类失败才改写签名结论（SCHEMA §9 约定；MAC/填充失败只进审计与响应）
_SIGNATURE_FAILURES = frozenset({"digest_mismatch", "bad_signature", "cert_invalid"})

#: 失败归类 → (HTTP code, failed_step 默认值)
_REASON_FAILURE: dict[str, tuple[int, str]] = {
    "bad_envelope": (40900, "envelope_lookup"),
    "bad_key": (50031, "unwrap_cek"),
    "mac_mismatch": (50030, "mac"),
    "bad_padding": (50031, "decrypt"),
    "digest_mismatch": (50021, "digest_recheck"),
    "bad_signature": (50021, "signature"),
    "cert_invalid": (50021, "cert_check"),
}


class VerificationFailedError(AppError):
    """验签/解密失败：HTTP 409（契约刻意让 HTTP 与业务码不完全对应），不返回任何明文。"""

    def __init__(self, reason_code: str, failed_step: str) -> None:
        code, _ = _REASON_FAILURE.get(reason_code, (40900, failed_step))
        self.code = code
        self.http_status = 409
        self.reason_code = reason_code
        self.failed_step = failed_step
        super().__init__(
            "验签或解密未通过",
            detail={"reason_code": reason_code, "failed_step": failed_step},
        )


def verify_report(
    db: Session,
    user: User,
    report_id: int,
    *,
    settings: Settings,
    store: CredentialStore,
    storage,
) -> VerifyData:
    form = get_visible_or_404(db, ReportForm, report_id, user)
    now = datetime.now(timezone.utc)
    provider = provider_for(settings)
    names = algo_names(provider)

    envelope = _form_envelope(db, form.id)
    signature = _active_signature(db, form.id)
    if envelope is None or signature is None or not envelope.ciphertext_b64:
        _fail(
            db, user, form, signature,
            reason="bad_envelope", step="envelope_lookup",
        )

    # 口令错误/未配置是 422，不是验签失败（契约 §5）；让它原样上抛
    finance_key = load_finance_private_key(db, envelope.recipient_cert_id, settings)

    # 1) 单据信封：解 CEK → 验 MAC/解 → payload_sha256
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
    except (EnvelopeOpenError, binascii.Error, ValueError) as exc:
        reason = exc.reason if isinstance(exc, EnvelopeOpenError) else "bad_envelope"
        _, default_step = _REASON_FAILURE.get(reason, (40900, "decrypt"))
        _fail(
            db, user, form, signature,
            reason=reason, step=default_step,
        )
        raise AssertionError("unreachable")  # pragma: no cover

    # 2) 影像信封：同一把财务私钥逐张解，解出的字节不能留在内存之外
    items = load_items(db, form.id)
    file_envelopes = _file_envelopes(db, [row.file.id for row in items])
    image_plaintexts: dict[int, bytes] = {}
    for row in items:
        file_envelope = file_envelopes.get(row.file.id)
        if file_envelope is None:
            _fail(
                db, user, form, signature,
                reason="bad_envelope", step="decrypt",
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
        except (EnvelopeOpenError, binascii.Error, ValueError) as exc:
            reason = exc.reason if isinstance(exc, EnvelopeOpenError) else "bad_envelope"
            failed_step = _REASON_FAILURE.get(reason, (40900, "decrypt"))[1]
            _fail(
                db, user, form, signature,
                reason=reason, step=failed_step,
            )
            raise AssertionError("unreachable")  # pragma: no cover
        image_plaintexts[row.file.id] = opened.plaintext

    # 3) 申请人证书（运行期口径，D-080：不做链验签）
    certificate = db.get(Certificate, signature.cert_id)
    if certificate is None:
        _fail(db, user, form, signature, reason="cert_invalid", step="cert_check")
    problem = _certificate_problem(certificate)
    if problem is not None or (certificate.key_algo or "").upper() != names.cert_algo:
        _fail(db, user, form, signature, reason="cert_invalid", step="cert_check")

    # 4) 验签：对**库里存的那份** canonical 字节
    canonical_bytes = (form.canonical_json or "").encode("utf-8")
    try:
        public_material = recipient_key_material(
            key_algo=certificate.key_algo, cert_pem=certificate.cert_pem
        )
        signature_bytes = base64.b64decode(signature.signature_b64, validate=True)
    except (KeyLoadError, binascii.Error, ValueError):
        _fail(db, user, form, signature, reason="bad_signature", step="signature")
    try:
        signature_ok = provider.verify(canonical_bytes, signature_bytes, public_material)
    except Exception:  # noqa: BLE001 - 验签异常一律按不通过
        signature_ok = False
    if not signature_ok:
        _fail(db, user, form, signature, reason="bad_signature", step="signature")

    # 5) 摘要重算：用库内当前数据 + canonical 里的 nonce 重建签名域，逐字节比对
    try:
        rebuilt = rebuild_signing_canonical(form, items, form.canonical_json or "")
    except ValueError:
        _fail(db, user, form, signature, reason="digest_mismatch", step="digest_recheck")
    if rebuilt != canonical_bytes or sm3_digest(canonical_bytes).hex() != signature.digest_hex:
        _fail(db, user, form, signature, reason="digest_mismatch", step="digest_recheck")
    payload_sha256_match = True
    for row in items:
        plaintext = image_plaintexts.get(row.file.id, b"")
        if hashlib.sha256(plaintext).hexdigest() != row.file.sha256:
            payload_sha256_match = False
    if not payload_sha256_match:
        _fail(db, user, form, signature, reason="digest_mismatch", step="digest_recheck")

    # 通过：审计（同事务）→ commit → 签发 verify_token
    append_audit(
        db,
        action="verify",
        trace_id=get_trace_id(),
        actor_id=user.id,
        actor_role=user.role,
        target_type="form",
        target_id=form.id,
        detail={
            "report_no": form.report_no,
            "result": "ok",
            "cert_fp": certificate.fingerprint_sha256,
            "payload_sha256_match": True,
        },
    )
    db.commit()

    token = secrets.token_urlsafe(24)
    ttl = settings.preview_token_ttl_seconds
    try:
        store.put(verify_key(form.id), token, ttl)
    except Exception as exc:  # noqa: BLE001 - Redis 不可用不会把已通过的验签说成失败
        logger.error(
            "verify_token 写入失败",
            extra={"event": "verify_token_store_failed", "report_id": form.id},
        )
        raise ServiceUnavailableError("凭据存储不可用，请重试") from exc

    logger.info(
        "财务验签通过",
        extra={"event": "finance_verify_ok", "report_id": form.id, "user_id": user.id},
    )
    return VerifyData(
        report_id=form.id,
        verified=True,
        steps=_all_steps_ok(),
        cert_fp=certificate.fingerprint_sha256,
        payload_sha256_match=True,
        verified_at=now,
        verify_token=token,
        verify_token_expires_in=ttl,
    )


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _fail(
    db: Session,
    user: User,
    form: ReportForm,
    signature: Signature | None,
    *,
    reason: str,
    step: str,
) -> None:
    """记录失败（审计 + 必要的签名结论）后抛 `VerificationFailedError`。"""
    if signature is not None and reason in _SIGNATURE_FAILURES:
        signature.verified = False
        signature.verify_result = reason
        signature.verified_at = datetime.now(timezone.utc)
        signature.verifier_id = None
    try:
        append_audit(
            db,
            action="verify",
            trace_id=get_trace_id(),
            actor_id=user.id,
            actor_role=user.role,
            target_type="form",
            target_id=form.id,
            detail={
                "report_no": form.report_no,
                "result": "failed",
                "reason_code": reason,
                "failed_step": step,
            },
        )
        db.commit()
    except Exception:  # noqa: BLE001 - 审计失败不能掩盖验签失败的原始结论
        db.rollback()
        logger.warning(
            "验签失败审计写入失败",
            extra={"event": "verify_audit_failed", "report_id": form.id},
        )
    failed = VerificationFailedError(reason, step)
    raise failed


def _all_steps_ok() -> list[VerifyStep]:
    return [VerifyStep(step=name, ok=True) for name in STEP_ORDER]


def _form_envelope(db: Session, form_id: int) -> Envelope | None:
    return db.scalars(select(Envelope).where(Envelope.form_id == form_id).limit(1)).first()


def _active_signature(db: Session, form_id: int) -> Signature | None:
    return db.scalars(
        select(Signature)
        .where(Signature.form_id == form_id, Signature.status == "active")
        .order_by(Signature.version.desc(), Signature.id.desc())
        .limit(1)
    ).first()


def _file_envelopes(db: Session, file_ids: list[int]) -> dict[int, Envelope]:
    if not file_ids:
        return {}
    rows = db.scalars(select(Envelope).where(Envelope.file_id.in_(file_ids))).all()
    return {row.file_id: row for row in rows if row.file_id is not None}


def _cert_fp(db: Session, cert_id: int) -> str:
    fingerprint = db.scalar(
        select(Certificate.fingerprint_sha256).where(Certificate.id == cert_id)
    )
    return fingerprint or ""


def _certificate_problem(certificate: Certificate) -> str | None:
    """吊销/有效期/指纹一致性；公钥与签名的绑定由验签环节保证。"""
    try:
        fingerprint = certificate_fingerprint(certificate.cert_pem)
    except KeyLoadError:
        return "cert_invalid"
    if fingerprint != certificate.fingerprint_sha256:
        return "cert_invalid"
    problem = certificate_problem(
        key_algo=certificate.key_algo,
        cert_pem=certificate.cert_pem,
        public_material=None,
        revoked=certificate.revoked,
    )
    return problem