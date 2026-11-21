"""证书接口（API.md §6）：列表 + 吊销；**不提供私钥导出**，签发走脚本。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.database import get_db
from app.core.exceptions import ok
from app.models.users import User
from app.schemas.certificate import CertListQuery, CertOut, CertRevokeRequest
from app.schemas.common import Envelope, PageResult
from app.services import certificate_service

router = APIRouter(prefix="/certs", tags=["certs"])


@router.get(
    "",
    response_model=Envelope[PageResult[CertOut]],
    summary="证书列表（admin 全部 / 本人）",
)
def list_certs(
    query: Annotated[CertListQuery, Query()],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(certificate_service.list_certificates(db, user, query))


@router.post(
    "/{cert_id}/revoke",
    response_model=Envelope[CertOut],
    summary="吊销证书（admin；已吊销幂等）",
)
def revoke_cert(
    cert_id: int,
    payload: CertRevokeRequest,
    user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    return ok(certificate_service.revoke_certificate(db, user, cert_id, payload.reason))