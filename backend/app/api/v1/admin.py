"""管理端接口（API.md §7）：审计日志列表与哈希链校验。

权限：整个路由 `require_roles("admin")`（角色级 403/40301，与本项目既有语义一致）。
只读接口，不提供审计写入口（写行由业务服务在同一事务里调用 `services.audit.append_audit`）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.database import get_db
from app.core.exceptions import ok
from app.models.users import User
from app.schemas.archive import ArchiveData, ArchiveRequest
from app.schemas.audit import AuditListQuery, AuditLogOut, AuditVerifyData
from app.schemas.common import Envelope, PageResult
from app.services import archive_service, audit

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_roles("admin"))])


@router.get("/audit", response_model=Envelope[PageResult[AuditLogOut]], summary="审计日志列表（admin）")
def list_audit(
    query: Annotated[AuditListQuery, Query()],
    db: Session = Depends(get_db),
) -> dict:
    return ok(audit.list_audit_logs(db, query))


@router.get(
    "/audit/verify-chain",
    response_model=Envelope[AuditVerifyData],
    summary="审计哈希链全量校验（admin）",
)
def verify_audit_chain(db: Session = Depends(get_db)) -> dict:
    result = audit.verify_chain(db)
    return ok(
        AuditVerifyData(
            ok=result.ok,
            checked_count=result.checked_count,
            broken_at=result.broken_at,
            broken_id=result.broken_id,
            reason=result.reason,
        )
    )


@router.post(
    "/reports/{report_id}/archive",
    response_model=Envelope[ArchiveData],
    summary="归档已审批单据（不可逆；必须 confirm=true）",
)
def archive_report(
    report_id: int,
    payload: ArchiveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(
        archive_service.archive_report(
            db, user, report_id, confirm=payload.confirm
        )
    )