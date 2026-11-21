"""S5-6 归档服务：不可逆归档、归档检索、口令加密归档包。

归档是**系统运维动作**：仅 admin 路由可达，直接按 id 查（admin 的常规业务读取仍是 404）。
导出则是业务动作：走 `get_visible_or_404`（本人 submitter / reviewer），且只允许已归档单据。
"""
from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictError,
    FieldConfirmationRequiredError,
    NotFoundError,
)
from app.core.logging import get_trace_id
from app.crypto.archive_crypto import seal_archive
from app.models.certificates import Certificate
from app.models.envelopes import Envelope
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.report_items import ReportItem
from app.models.signatures import Signature
from app.models.users import User
from app.schemas.archive import ArchiveData, ArchiveSearchQuery
from app.schemas.common import PageResult
from app.schemas.report import ReportOut
from app.services.audit import append_audit
from app.services.pagination import paginate
from app.services.report_service import transition
from app.services.repository import filter_visible, get_visible_or_404
from app.utils.text import escape_like
from app.utils.time import BUSINESS_TZ, business_day_bounds

logger = logging.getLogger("app.reports.archive")

_ARCHIVE_SORT = {
    "archive_at": ReportForm.archive_at,
    "total_amount": ReportForm.total_amount,
    "submitted_at": ReportForm.submitted_at,
}


def archive_report(
    db: Session, user: User, report_id: int, *, confirm: bool
) -> ArchiveData:
    """approved → archived（不可逆）；并发只允许一个成功。"""
    if not confirm:
        raise FieldConfirmationRequiredError("归档是不可逆操作，必须带 confirm=true")

    form = db.get(ReportForm, report_id)
    if form is None:
        raise NotFoundError("报销单不存在")

    try:
        form = db.execute(
            select(ReportForm)
            .where(ReportForm.id == form.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()

        if form.status == "archived":
            raise ConflictError(
                "单据已归档，不能重复归档",
                detail={"reason_code": "already_archived"},
            )
        if form.status != "approved":
            raise ConflictError(
                f"只有已审批通过的单据可归档（当前 {form.status}）",
                detail={"reason_code": "status_not_archivable"},
            )

        # 业务年（Asia/Shanghai）+ 全局序列；与 report_no 同一取舍（只保证唯一，不保证按年重置）
        sequence = db.execute(text("select nextval('archive_no_seq')")).scalar_one()
        year = datetime.now(BUSINESS_TZ).year
        archive_no = f"AR-{year}-{int(sequence):04d}"
        archive_at = datetime.now(timezone.utc)

        form.archive_no = archive_no
        form.archive_at = archive_at
        transition(db, form, "archived", user, bump_version=False)
        form.version += 1

        append_audit(
            db,
            action="archive",
            trace_id=get_trace_id(),
            actor_id=user.id,
            actor_role=user.role,
            target_type="form",
            target_id=form.id,
            detail={
                "report_no": form.report_no,
                "archive_no": archive_no,
                "archive_at": archive_at.isoformat(),
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(form)
    logger.info(
        "报销单归档",
        extra={
            "event": "report_archived",
            "report_id": form.id,
            "archive_no": archive_no,
            "user_id": user.id,
        },
    )
    return ArchiveData(
        report_id=form.id,
        report_no=form.report_no,
        status=form.status,
        archive_no=archive_no,
        archive_at=archive_at,
    )


def search_reports(
    db: Session, user: User, query: ArchiveSearchQuery
) -> PageResult[ReportOut]:
    """归档检索：submitter 本人 / reviewer 全部；只查 archived。"""
    stmt = (
        select(ReportForm, User.dept)
        .join(User, User.id == ReportForm.applicant_id)
        .where(ReportForm.status == "archived")
    )
    stmt = filter_visible(stmt, ReportForm, user)

    if query.q:
        pattern = escape_like(query.q)
        stmt = stmt.where(
            (ReportForm.report_no.ilike(pattern, escape="\\"))
            | (User.username.ilike(pattern, escape="\\"))
            | (User.real_name.ilike(pattern, escape="\\"))
        )
    if query.date_from is not None:
        start, _ = business_day_bounds(query.date_from, query.date_from)
        stmt = stmt.where(ReportForm.archive_at >= start)
    if query.date_to is not None:
        _, end = business_day_bounds(query.date_to, query.date_to)
        stmt = stmt.where(ReportForm.archive_at < end)
    if query.amount_min is not None:
        stmt = stmt.where(ReportForm.total_amount >= query.amount_min)
    if query.amount_max is not None:
        stmt = stmt.where(ReportForm.total_amount <= query.amount_max)

    result = paginate(
        db,
        stmt,
        ReportForm,
        _ARCHIVE_SORT,
        query,
        default="archive_at",
        scalar=False,
        nulls_last=True,
    )
    items = [
        ReportOut.model_validate(form).model_copy(update={"department": dept})
        for form, dept in result.items
    ]
    return PageResult[ReportOut](
        items=items,
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


def export_archive(
    db: Session, user: User, report_id: int, passphrase: str
) -> tuple[bytes, str]:
    """生成**口令加密**的归档包：内层是标准 ZIP，外层是自描述 AES-GCM 容器。"""
    form = get_visible_or_404(db, ReportForm, report_id, user)
    if form.status != "archived" or not form.archive_no:
        raise ConflictError(
            "只有已归档单据可导出归档包",
            detail={"reason_code": "not_archived"},
        )

    applicant = db.get(User, form.applicant_id)
    items = _load_items(db, form.id)
    signature = _active_signature(db, form.id)
    certificate = db.get(Certificate, signature.cert_id) if signature else None
    form_envelope = _form_envelope(db, form.id)
    file_envelopes = _file_envelopes(db, [row_file.id for _item, _invoice, row_file in items])
    cert_fps = _cert_fps(
        db,
        [envelope.recipient_cert_id for envelope in [form_envelope, *file_envelopes.values()] if envelope],
    )

    manifest_rows = [
        [
            form.archive_no,
            form.report_no,
            _applicant_name(applicant),
            f"{form.total_amount:.2f}",
            str(form.item_count),
            _local_text(form.archive_at),
            signature.verify_result if signature else "unverified",
        ]
    ]
    archive_doc = {
        "format": "study-ocr-archive-v1",
        "report": {
            "report_no": form.report_no,
            "archive_no": form.archive_no,
            "archive_at": form.archive_at.isoformat() if form.archive_at else None,
            "status": form.status,
            "applicant_id": form.applicant_id,
            "applicant_name": _applicant_name(applicant),
            "department": applicant.dept if applicant else None,
            "title": form.title,
            "reason": form.reason,
            "total_amount": f"{form.total_amount:.2f}",
            "item_count": form.item_count,
            "created_at": form.created_at.isoformat(),
            "submitted_at": form.submitted_at.isoformat() if form.submitted_at else None,
        },
        "items": [
            {
                "invoice_id": invoice.id,
                "invoice_code": invoice.invoice_code,
                "invoice_no": invoice.invoice_no,
                "invoice_type": invoice.invoice_type,
                "amount": f"{item.amount:.2f}",
                "file_sha256": row_file.sha256,
                "file_mime": row_file.mime,
                "file_size": row_file.size,
            }
            for item, invoice, row_file in items
        ],
        "signature": _signature_doc(signature, certificate),
        "envelopes": {
            "form": _envelope_doc(form_envelope, cert_fps),
            "files": [
                {"file_id": file_id, **_envelope_doc(envelope, cert_fps)}
                for file_id, envelope in sorted(file_envelopes.items())
            ],
        },
        "note": "本归档包不含任何影像明文；影像明文仅经财务 verify/decrypt 的 30 分钟临时预览获取。",
    }

    zip_bytes = _build_zip(
        archive_doc=archive_doc,
        manifest_rows=manifest_rows,
        canonical_json=form.canonical_json or "",
        signature=signature,
        certificate=certificate,
    )
    encrypted = seal_archive(zip_bytes, passphrase)
    logger.info(
        "报销单归档包导出",
        extra={"event": "archive_exported", "report_id": form.id, "user_id": user.id},
    )
    return encrypted, f"{form.report_no}-archive.zip.enc"


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _build_zip(
    *,
    archive_doc: dict,
    manifest_rows: list[list[str]],
    canonical_json: str,
    signature: Signature | None,
    certificate: Certificate | None,
) -> bytes:
    buffer = io.BytesIO()
    manifest = io.StringIO()
    writer = csv.writer(manifest, lineterminator="\r\n")
    writer.writerow(["归档号", "单号", "申请人", "金额", "票据数", "归档时间", "验签结论"])
    writer.writerows(manifest_rows)
    manifest_bytes = ("\ufeff" + manifest.getvalue()).encode("utf-8")

    signature_doc = {
        "signature": _signature_doc(signature, certificate),
        "certificate_pem": certificate.cert_pem if certificate else None,
    }
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("archive.json", json.dumps(archive_doc, ensure_ascii=False, indent=2))
        zf.writestr("manifest.csv", manifest_bytes)
        zf.writestr("canonical.json", canonical_json)
        zf.writestr(
            "signatures/signature.json",
            json.dumps(signature_doc, ensure_ascii=False, indent=2),
        )
    return buffer.getvalue()


def _signature_doc(
    signature: Signature | None, certificate: Certificate | None
) -> dict | None:
    if signature is None:
        return None
    return {
        "algo": signature.algo,
        "digest_algo": signature.digest_algo,
        "digest_hex": signature.digest_hex,
        "signature_b64": signature.signature_b64,
        "cert_id": signature.cert_id,
        "cert_fingerprint": certificate.fingerprint_sha256 if certificate else None,
        "verified": signature.verified,
        "verify_result": signature.verify_result,
        "version": signature.version,
        "created_at": signature.created_at.isoformat() if signature.created_at else None,
    }


def _envelope_doc(
    envelope: Envelope | None, cert_fps: dict[int, str] | None = None
) -> dict | None:
    if envelope is None:
        return None
    return {
        "key_algo": envelope.key_algo,
        "cipher_algo": envelope.cipher_algo,
        "payload_sha256": envelope.payload_sha256,
        "recipient_cert_fp": (cert_fps or {}).get(envelope.recipient_cert_id),
        "ciphertext_len": len(envelope.ciphertext_b64 or ""),
    }


def _load_items(db: Session, form_id: int) -> list:
    rows = db.execute(
        select(ReportItem, Invoice, InvoiceFile)
        .join(Invoice, Invoice.id == ReportItem.invoice_id)
        .join(InvoiceFile, InvoiceFile.id == Invoice.file_id)
        .where(ReportItem.form_id == form_id)
        .order_by(ReportItem.id)
    ).all()
    return [(item, invoice, row_file) for item, invoice, row_file in rows]


def _active_signature(db: Session, form_id: int) -> Signature | None:
    return db.scalars(
        select(Signature)
        .where(Signature.form_id == form_id, Signature.status == "active")
        .order_by(Signature.version.desc(), Signature.id.desc())
        .limit(1)
    ).first()


def _form_envelope(db: Session, form_id: int) -> Envelope | None:
    return db.scalars(select(Envelope).where(Envelope.form_id == form_id).limit(1)).first()


def _file_envelopes(db: Session, file_ids: list[int]) -> dict[int, Envelope]:
    if not file_ids:
        return {}
    rows = db.scalars(select(Envelope).where(Envelope.file_id.in_(file_ids))).all()
    return {row.file_id: row for row in rows if row.file_id is not None}


def _applicant_name(applicant: User | None) -> str:
    if applicant is None:
        return ""
    return applicant.real_name or applicant.username


def _local_text(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(BUSINESS_TZ).strftime("%Y-%m-%d %H:%M:%S")

def _cert_fps(db: Session, cert_ids: list[int]) -> dict[int, str]:
    ids = {cert_id for cert_id in cert_ids if cert_id}
    if not ids:
        return {}
    rows = db.execute(
        select(Certificate.id, Certificate.fingerprint_sha256).where(
            Certificate.id.in_(ids)
        )
    ).all()
    return {cert_id: fingerprint for cert_id, fingerprint in rows}
