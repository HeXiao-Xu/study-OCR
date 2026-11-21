"""财务审核接口（API.md §5，S5-3）：待审列表 / 待审导出 / 单据详情（元数据）。

纪律：
- 仅 `reviewer`（`require_roles` → 403；admin 也不得代审）；
- 列表与详情**永不**返回密文、会话密钥、对象名或明文；
- `/pending`、`/pending/export` 必须先于 `/{report_id}` 注册，否则 "pending" 会被动态段吃掉。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.database import get_db
from app.core.exceptions import ok
from app.models.users import User
from app.schemas.common import Envelope, PageResult
from app.schemas.finance import (
    FinanceDetailData,
    FinancePendingItem,
    FinancePendingQuery,
)
from app.services import finance_service
from app.utils.time import BUSINESS_TZ

router = APIRouter(prefix="/finance", tags=["finance"])

_REVIEWER = Depends(require_roles("reviewer"))


@router.get(
    "/pending",
    response_model=Envelope[PageResult[FinancePendingItem]],
    summary="财务待审列表（只读；verify_status 取自已落库的签名结论）",
)
def list_pending(
    query: Annotated[FinancePendingQuery, Query()],
    user: User = _REVIEWER,
    db: Session = Depends(get_db),
) -> dict:
    return ok(finance_service.list_pending(db, user, query))


@router.get(
    "/pending/export",
    response_class=StreamingResponse,
    summary="待审清单 CSV（UTF-8 BOM + CRLF，Excel 直接可读）",
)
def export_pending(
    query: Annotated[FinancePendingQuery, Query()],
    user: User = _REVIEWER,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    # 取数与上限检查在返回响应前完成（流式阶段不再碰数据库会话）
    chunks = finance_service.export_pending_csv(db, user, query)
    stamp = datetime.now(BUSINESS_TZ).strftime("%Y%m%d")
    return StreamingResponse(
        iter(chunks),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="finance-pending-{stamp}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/{report_id}",
    response_model=Envelope[FinanceDetailData],
    summary="财务单据详情（单据 + 明细 + 附件元数据；明文走 S5-4）",
)
def get_finance_detail(
    report_id: int,
    user: User = _REVIEWER,
    db: Session = Depends(get_db),
) -> dict:
    return ok(finance_service.get_detail(db, user, report_id))