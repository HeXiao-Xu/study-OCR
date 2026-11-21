"""证书管理服务（S4-6 补 API）：只读列表 + 吊销；签发仍走 `scripts/register_identity.py`（私钥不出服务端）。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, RoleForbiddenError
from app.models.certificates import Certificate
from app.models.users import User
from app.schemas.certificate import CertListQuery, CertOut
from app.schemas.common import PageResult
from app.services.audit import append_audit
from app.services.pagination import paginate
from app.core.logging import get_trace_id

_SORT = {"created_at": Certificate.created_at, "not_after": Certificate.not_after}


def list_certificates(db: Session, user: User, query: CertListQuery) -> PageResult[CertOut]:
    stmt = select(Certificate)
    if user.role != "admin":
        if query.owner_type == "server":
            raise RoleForbiddenError("无权查看服务器证书")
        stmt = stmt.where(
            Certificate.owner_type == "user", Certificate.owner_id == user.id
        )
    elif query.owner_type is not None:
        stmt = stmt.where(Certificate.owner_type == query.owner_type)
    if query.revoked is not None:
        stmt = stmt.where(Certificate.revoked.is_(query.revoked))

    page = paginate(db, stmt, Certificate, _SORT, query, default="created_at")
    return PageResult[CertOut](
        items=[CertOut.model_validate(row) for row in page.items],
        total=page.total,
        page=page.page,
        page_size=page.page_size,
    )


def revoke_certificate(
    db: Session, user: User, cert_id: int, reason: str
) -> CertOut:
    cert = db.get(Certificate, cert_id)
    if cert is None:
        raise NotFoundError("证书不存在")
    if cert.revoked:
        return CertOut.model_validate(cert)

    cert.revoked = True
    cert.revoked_at = datetime.now(timezone.utc)
    append_audit(
        db,
        action="revoke_cert",
        trace_id=get_trace_id(),
        actor_id=user.id,
        actor_role=user.role,
        target_type="certificate",
        target_id=cert.id,
        detail={"reason": reason, "fingerprint": cert.fingerprint_sha256},
    )
    db.commit()
    db.refresh(cert)
    return CertOut.model_validate(cert)