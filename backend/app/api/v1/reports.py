"""报销单接口（API.md §4，S5-1）：建单 / 加明细 / 详情 / 我的列表。

路由只做参数校验与调用；单号、状态机、金额汇总、行级隔离都在 `services/report_service.py`。
`/mine` 必须注册在 `/{report_id}` 之前，否则 "mine" 会先被动态段匹配走。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import ok
from app.models.users import User
from app.schemas.common import Envelope, PageResult
from app.schemas.report import (
    ReportCreateRequest,
    ReportDetailData,
    ReportItemCreateRequest,
    ReportListQuery,
    ReportOut,
    SignData,
    SignRequest,
    SubmitData,
    SubmitRequest,
)
from app.services import report_service, submit_service
from app.services.storage import StorageService, get_storage

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[ReportOut],
    summary="创建报销单（draft；合计由明细汇总）",
)
def create_report(
    payload: ReportCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(report_service.create_report(db, user, payload))


@router.get(
    "/mine",
    response_model=Envelope[PageResult[ReportOut]],
    summary="我的报销单（分页/状态/日期；不含明细）",
)
def list_my_reports(
    query: Annotated[ReportListQuery, Query()],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(report_service.list_my_reports(db, user, query))


@router.get(
    "/{report_id}",
    response_model=Envelope[ReportDetailData],
    summary="报销单详情（本人 / reviewer 仅 submitted 之后）",
)
def get_report(
    report_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(report_service.get_report_detail(db, user, report_id))


@router.post(
    "/{report_id}/items",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[ReportOut],
    summary="加明细（仅本人 draft/rejected；票据置 in_report）",
)
def add_report_item(
    report_id: int,
    payload: ReportItemCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(report_service.add_item(db, user, report_id, payload))


@router.post(
    "/{report_id}/sign",
    response_model=Envelope[SignData],
    summary="签名单据（SM2-SM3 / RSA-PSS；口令只在内存）",
)
def sign_report(
    report_id: int,
    payload: SignRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return ok(
        submit_service.sign_report(db, user, report_id, payload, settings=settings)
    )


@router.post(
    "/{report_id}/submit",
    response_model=Envelope[SubmitData],
    summary="提交：签名校验 → 数字信封（影像转密文）→ submitted（同事务、幂等）",
)
def submit_report(
    report_id: int,
    payload: SubmitRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
) -> dict:
    return ok(
        submit_service.submit_report(
            db, user, report_id, payload, settings=settings, storage=storage
        )
    )