"""报销单签名与提交（S5-2）：一次事务内完成签名 → 封数字信封 → 推进状态 → 审计。

三条不变量（对应任务卡的坑）：

1. **没有半成品**：签名行、单据字段、单据/文件信封、状态、审计全部在同一事务里，失败整体回滚；
   信封写 MinIO 的对象在回滚分支里补偿删除。
2. **签名域不被重新拼装**：`canonical_json` 原样落 `report_forms.canonical_json`；提交时验签用**库里那份字节**，
   不是现场重拼——否则"改规则就历史签名全失效"。
3. **影像只留密文**：明文对象在**提交成功之后**才删（失败只告警，不回滚已提交事务），
   并把 `invoice_files.storage_key/is_cipher` 指向密文对象。

提交幂等（API.md §4）：同单同键重复提交 → 200 + `duplicated=true` + 既有 id；
键被别的单据占用 → 409 + `reason=idempotency_key_reused`。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import ConflictError, SignatureFailedError
from app.core.logging import get_trace_id
from app.crypto.canonical import canonical_json
from app.crypto.key_loader import KeyLoadError, recipient_key_material
from app.crypto.sm.sm3 import sm3_digest
from app.models.certificates import Certificate
from app.models.enums import (
    AuditAction,
    AuditTargetType,
    ExtractStatus,
    InvoiceStatus,
    ReportStatus,
    SignatureStatus,
)
from app.models.envelopes import Envelope
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.report_items import ReportItem
from app.models.signatures import Signature
from app.models.users import User
from app.schemas.report import SignData, SignRequest, SubmitData, SubmitRequest
from app.services.audit import append_audit
from app.services.key_service import (
    get_finance_recipient,
    get_signing_identity,
    load_signing_key,
    provider_for,
)
from app.services.report_crypto import (
    SubmitItem,
    algo_names,
    build_sealed_report,
    canonical_signing_bytes,
    seal_payload,
)
from app.services.report_service import require_submitter, transition
from app.services.repository import get_visible_or_404
from app.services.storage import StorageService

logger = logging.getLogger("app.reports")

#: 可签名的状态（API.md §4：draft / pending_sign / rejected；signed 时是"重签"）
SIGNABLE_STATUSES = frozenset(
    {
        ReportStatus.DRAFT.value,
        ReportStatus.PENDING_SIGN.value,
        ReportStatus.REJECTED.value,
        ReportStatus.SIGNED.value,
    }
)
#: 已提交（及之后）的状态：重复提交在这里判幂等/冲突
SUBMITTED_LIKE = frozenset(
    {
        ReportStatus.SUBMITTED.value,
        ReportStatus.APPROVED.value,
        ReportStatus.ARCHIVED.value,
    }
)


# --------------------------------------------------------------------------- #
# 签名
# --------------------------------------------------------------------------- #
def sign_report(
    db: Session, user: User, report_id: int, payload: SignRequest, *, settings: Settings
) -> SignData:
    form = get_visible_or_404(db, ReportForm, report_id, user)
    require_submitter(user, action="签署报销单")

    if form.status not in SIGNABLE_STATUSES:
        raise ConflictError(f"单据当前状态为 {form.status}，不允许签名")
    items = load_items(db, form.id)
    if not items:
        raise ConflictError("单据没有明细，不能签名")
    _ensure_invoices_confirmed(items)

    provider = provider_for(settings)
    names = algo_names(provider)
    identity = get_signing_identity(db, user, provider)
    # 口令错误 → 42200；证书不可用 → 40900（都不落半成品）
    material = load_signing_key(identity, payload.private_key_passphrase, settings)

    nonce = secrets.token_hex(16)
    canonical = canonical_signing_bytes(form, items, nonce=nonce)
    digest_hex = _sm3_hex(canonical)
    try:
        signature = provider.sign(canonical, material)
        if not provider.verify(canonical, signature, material):
            raise SignatureFailedError("签名自检失败")
    except SignatureFailedError:
        raise
    except Exception as exc:  # noqa: BLE001 - 归一到 50020，不泄露密钥/载荷
        raise SignatureFailedError() from exc
    signature_b64 = base64.b64encode(signature).decode("ascii")

    try:
        # 先作废旧 active 签名再插入：部分唯一索引只允许一条 active（SCHEMA §5.3）
        db.execute(
            update(Signature)
            .where(
                Signature.form_id == form.id,
                Signature.status == SignatureStatus.ACTIVE.value,
            )
            .values(status=SignatureStatus.VOID.value)
        )
        db.flush()
        next_version = (
            int(
                db.scalar(
                    select(func.coalesce(func.max(Signature.version), 0)).where(
                        Signature.form_id == form.id
                    )
                )
                or 0
            )
            + 1
        )
        row = Signature(
            form_id=form.id,
            signer_id=user.id,
            algo=names.signature_algo,
            digest_algo="SM3",
            digest_hex=digest_hex,
            signature_b64=signature_b64,
            cert_id=identity.certificate.id,
            version=next_version,
            status=SignatureStatus.ACTIVE.value,
            # 签名方不算验签方：verified/verify_result 留给 S5-4 独立验签后写
            verified=False,
        )
        db.add(row)
        db.flush()

        if form.status in (ReportStatus.DRAFT.value, ReportStatus.REJECTED.value):
            transition(db, form, ReportStatus.PENDING_SIGN.value, user, bump_version=False)
        transition(db, form, ReportStatus.SIGNED.value, user, bump_version=False)
        form.canonical_json = canonical.decode("utf-8")
        form.digest_algo = "SM3"
        form.digest_hex = digest_hex
        form.version += 1  # 一次签名操作只 +1（D-085）

        append_audit(
            db,
            action=AuditAction.SIGN.value,
            trace_id=get_trace_id(),
            actor_id=user.id,
            actor_role=user.role,
            target_type=AuditTargetType.SIGNATURE.value,
            target_id=row.id,
            detail={
                "report_no": form.report_no,
                "digest_hex": digest_hex,
                "cert_id": identity.certificate.id,
                "algo": names.signature_algo,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("签名写入冲突，请重试") from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(row)
    db.refresh(form)
    logger.info(
        "报销单签名",
        extra={
            "event": "report_signed",
            "report_id": form.id,
            "user_id": user.id,
            "signature_id": row.id,
        },
    )
    return SignData(
        signature_id=row.id,
        digest_algo="SM3",
        digest_hex=digest_hex,
        algo=names.signature_algo,
        signature_b64=signature_b64,
        cert_id=identity.certificate.id,
        canonical_sha256=hashlib.sha256(canonical).hexdigest(),
        status=form.status,
    )


# --------------------------------------------------------------------------- #
# 提交
# --------------------------------------------------------------------------- #
def submit_report(
    db: Session,
    user: User,
    report_id: int,
    payload: SubmitRequest,
    *,
    settings: Settings,
    storage: StorageService,
) -> SubmitData:
    form = get_visible_or_404(db, ReportForm, report_id, user)
    require_submitter(user, action="提交报销单")

    provider = provider_for(settings)
    names = algo_names(provider)
    now = datetime.now(timezone.utc)
    new_objects: list[str] = []
    old_objects: list[str] = []

    try:
        # 先锁单据：并发提交/幂等判定都在锁内（D-084 的同一把锁顺序：单据 → 后续）
        form = db.execute(
            select(ReportForm)
            .where(ReportForm.id == form.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()

        duplicated = _idempotent_result(db, form, payload.idempotency_key)
        if duplicated is not None:
            return duplicated

        if form.status != ReportStatus.SIGNED.value:
            raise ConflictError(f"单据当前状态为 {form.status}，未签名不能提交")
        if payload.version is not None and payload.version != form.version:
            raise ConflictError(f"版本冲突：当前 version={form.version}")

        signature = _active_signature(db, form.id)
        if signature is None:
            raise ConflictError("单据没有有效签名")
        if not form.canonical_json:
            raise ConflictError("单据缺少签名载荷，请重新签名")
        canonical_bytes = form.canonical_json.encode("utf-8")
        digest_hex = _sm3_hex(canonical_bytes)
        if digest_hex != signature.digest_hex:
            raise ConflictError(
                "签名载荷摘要不符（单据内容被改过）", detail={"reason": "digest_mismatch"}
            )

        signer_cert = db.get(Certificate, signature.cert_id)
        if signer_cert is None:
            raise ConflictError("签名证书不存在")
        if (signer_cert.key_algo or "").upper() != names.cert_algo:
            raise ConflictError("签名证书算法与当前配置不一致")
        if not _verify_signature(provider, signature, signer_cert, canonical_bytes):
            raise ConflictError("签名校验失败", detail={"reason": "bad_signature"})

        # SCHEMA §5.3："submitted 之后必须已验证"——提交时系统已用证书公钥验过签，
        # 把结论落库（verifier_id 为空 = 系统自动验签）；财务端独立验签（S5-4）再改写失败态。
        signature.verified = True
        signature.verify_result = "ok"
        signature.verified_at = now
        signature.verifier_id = None

        finance_cert, finance_material = get_finance_recipient(db, provider, now=now)

        items = load_items(db, form.id)
        if not items:
            raise ConflictError("单据没有明细，不能提交")
        if _form_envelope(db, form.id) is not None:
            raise ConflictError("单据已有信封（不支持重复封装）")

        applicant = db.get(User, form.applicant_id)
        sealed_report = build_sealed_report(
            form,
            items,
            applicant_username=applicant.username if applicant is not None else "",
            applicant_dept=applicant.dept if applicant is not None else None,
            joined_fields=_fields_by_invoice(db, [row.invoice.id for row in items]),
            signature=signature,
            signature_cert_fp=signer_cert.fingerprint_sha256,
            submitted_at=now,
        )
        report_bytes = _canonical_bytes(sealed_report)
        form_sealed = seal_payload(
            provider=provider,
            recipient=finance_material,
            recipient_cert_fp=finance_cert.fingerprint_sha256,
            plaintext=report_bytes,
            now=now,
        )
        form_envelope = Envelope(
            form_id=form.id,
            file_id=None,
            key_algo=form_sealed.key_algo,
            cipher_algo=form_sealed.cipher_algo,
            wrapped_key_b64=form_sealed.wrapped_key_b64,
            iv_b64=form_sealed.iv_b64,
            mac_b64=form_sealed.mac_b64,
            ciphertext_b64=base64.b64encode(form_sealed.ciphertext).decode("ascii"),
            ciphertext_storage_key=None,
            payload_sha256=form_sealed.payload_sha256,
            recipient_cert_id=finance_cert.id,
        )
        db.add(form_envelope)
        db.flush()

        # 先做完全部前置检查再动 MinIO：避免"封了一半才发现重复信封"
        for row in items:
            if row.file.is_cipher:
                raise ConflictError(f"票据 {row.invoice.id} 的影像已是密文（数据异常）")
            if db.scalar(
                select(Envelope.id).where(Envelope.file_id == row.file.id).limit(1)
            ) is not None:
                raise ConflictError(f"票据 {row.invoice.id} 的影像已有信封（不支持重复封装）")

        for row in items:
            plaintext = storage.get_bytes(
                row.file.storage_key, max_bytes=settings.max_upload_mb * 1024 * 1024
            )
            sealed = seal_payload(
                provider=provider,
                recipient=finance_material,
                recipient_cert_fp=finance_cert.fingerprint_sha256,
                plaintext=plaintext,
                now=now,
            )
            cipher_key = _cipher_object_key(now)
            storage.put(
                cipher_key,
                io.BytesIO(sealed.ciphertext),
                len(sealed.ciphertext),
                "application/octet-stream",
            )
            new_objects.append(cipher_key)
            db.add(
                Envelope(
                    form_id=None,
                    file_id=row.file.id,
                    key_algo=sealed.key_algo,
                    cipher_algo=sealed.cipher_algo,
                    wrapped_key_b64=sealed.wrapped_key_b64,
                    iv_b64=sealed.iv_b64,
                    mac_b64=sealed.mac_b64,
                    ciphertext_b64=None,
                    ciphertext_storage_key=cipher_key,
                    payload_sha256=sealed.payload_sha256,
                    recipient_cert_id=finance_cert.id,
                )
            )
            old_objects.append(row.file.storage_key)
            # SCHEMA §3.1：storage_key 必须指向密文
            row.file.storage_key = cipher_key
            row.file.is_cipher = True

        transition(db, form, ReportStatus.ENCRYPTED.value, user, bump_version=False)
        transition(db, form, ReportStatus.SUBMITTED.value, user, bump_version=False)
        form.submitted_at = now
        form.idempotency_key = payload.idempotency_key
        form.version += 1  # 一次提交操作只 +1

        append_audit(
            db,
            action=AuditAction.SEAL.value,
            trace_id=get_trace_id(),
            actor_id=user.id,
            actor_role=user.role,
            target_type=AuditTargetType.ENVELOPE.value,
            target_id=form_envelope.id,
            detail={
                "report_no": form.report_no,
                "file_count": len(items),
                "key_algo": form_sealed.key_algo,
                "cipher_algo": form_sealed.cipher_algo,
            },
        )
        append_audit(
            db,
            action=AuditAction.SUBMIT.value,
            trace_id=get_trace_id(),
            actor_id=user.id,
            actor_role=user.role,
            target_type=AuditTargetType.FORM.value,
            target_id=form.id,
            detail={
                "report_no": form.report_no,
                "submitted_at": now.isoformat(),
                "item_count": form.item_count,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        # 补偿：本次写入的密文对象不能留在桶里
        for key in new_objects:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001 - 补偿失败只告警，不掩盖原始异常
                logger.warning(
                    "提交回滚后密文对象清理失败",
                    extra={"event": "cipher_object_cleanup_failed", "report_id": report_id},
                )
        raise

    # 提交成功之后才删明文：删失败只告警（明文不再被任何库行引用）
    for key in old_objects:
        try:
            storage.delete(key)
        except Exception:  # noqa: BLE001
            logger.warning(
                "提交后明文对象删除失败",
                extra={"event": "plaintext_object_delete_failed", "report_id": form.id},
            )

    db.refresh(form)
    db.refresh(signature)
    logger.info(
        "报销单提交",
        extra={
            "event": "report_submitted",
            "report_id": form.id,
            "user_id": user.id,
            "envelope_id": form_envelope.id,
        },
    )
    return SubmitData(
        report_id=form.id,
        report_no=form.report_no,
        status=form.status,
        signature_id=signature.id,
        envelope_id=form_envelope.id,
        submitted_at=form.submitted_at or now,
        duplicated=False,
    )


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _idempotent_result(
    db: Session, form: ReportForm, idempotency_key: str
) -> SubmitData | None:
    """同单同键重复提交 → 既有结果；键被别的单据占用 → 冲突。"""
    other = db.scalar(
        select(ReportForm.id)
        .where(
            ReportForm.idempotency_key == idempotency_key,
            ReportForm.id != form.id,
        )
        .limit(1)
    )
    if other is not None:
        raise ConflictError(
            "幂等键已被其他单据使用", detail={"reason": "idempotency_key_reused"}
        )
    if form.idempotency_key == idempotency_key and form.status in SUBMITTED_LIKE:
        signature = _active_signature(db, form.id)
        envelope = _form_envelope(db, form.id)
        if signature is None or envelope is None:
            raise ConflictError("单据已提交但签名或信封缺失（数据异常）")
        return SubmitData(
            report_id=form.id,
            report_no=form.report_no,
            status=form.status,
            signature_id=signature.id,
            envelope_id=envelope.id,
            submitted_at=form.submitted_at or datetime.now(timezone.utc),
            duplicated=True,
        )
    if form.status in SUBMITTED_LIKE:
        raise ConflictError(f"单据当前状态为 {form.status}，不能重复提交")
    return None


def _active_signature(db: Session, form_id: int) -> Signature | None:
    return db.scalars(
        select(Signature)
        .where(
            Signature.form_id == form_id,
            Signature.status == SignatureStatus.ACTIVE.value,
        )
        .order_by(Signature.version.desc(), Signature.id.desc())
        .limit(1)
    ).first()


def _form_envelope(db: Session, form_id: int) -> Envelope | None:
    return db.scalars(select(Envelope).where(Envelope.form_id == form_id).limit(1)).first()


def _verify_signature(
    provider, signature: Signature, certificate: Certificate, payload: bytes
) -> bool:
    try:
        public = recipient_key_material(
            key_algo=certificate.key_algo, cert_pem=certificate.cert_pem
        )
        signature_bytes = base64.b64decode(signature.signature_b64, validate=True)
    except (KeyLoadError, binascii.Error, ValueError):
        return False
    try:
        return bool(provider.verify(payload, signature_bytes, public))
    except Exception:  # noqa: BLE001 - 验签异常一律按"不通过"处理
        return False


def load_items(db: Session, form_id: int) -> list[SubmitItem]:
    rows = db.execute(
        select(ReportItem, Invoice, InvoiceFile)
        .join(Invoice, ReportItem.invoice_id == Invoice.id)
        .join(InvoiceFile, Invoice.file_id == InvoiceFile.id)
        .where(ReportItem.form_id == form_id)
        .order_by(ReportItem.id.asc())
    ).all()
    return [
        SubmitItem(item=row[0], invoice=row[1], file=row[2]) for row in rows
    ]


def _fields_by_invoice(
    db: Session, invoice_ids: list[int]
) -> dict[int, dict[str, str]]:
    if not invoice_ids:
        return {}
    result: dict[int, dict[str, str]] = {}
    rows = db.scalars(
        select(InvoiceField)
        .where(InvoiceField.invoice_id.in_(invoice_ids))
        .order_by(InvoiceField.id.asc())
    ).all()
    for row in rows:
        value = row.normalized_value if row.normalized_value is not None else row.field_value
        if value is None:
            continue
        result.setdefault(row.invoice_id, {})[row.field_name] = value
    return result


def _ensure_invoices_confirmed(items: list[SubmitItem]) -> None:
    allowed = {InvoiceStatus.CONFIRMED.value, InvoiceStatus.IN_REPORT.value}
    for row in items:
        if (
            row.invoice.status not in allowed
            or row.invoice.extract_status != ExtractStatus.CONFIRMED.value
        ):
            raise ConflictError(f"票据 {row.invoice.id} 未完成确认，不能签名")


def _cipher_object_key(now: datetime) -> str:
    """影像密文对象名：不用文件名、不用哈希前缀（避免泄露票据哈希）。"""
    return f"reports/{now:%Y/%m}/{secrets.token_hex(16)}.bin.cipher"


def _sm3_hex(data: bytes) -> str:
    return sm3_digest(data).hex()


def _canonical_bytes(obj: object) -> bytes:
    return canonical_json(obj)