"""财务审核接口（API.md §5，S5-3）：待审列表 / 待审导出 / 单据详情（元数据）。

纪律：
- 仅 `reviewer`（`require_roles` → 403；admin 也不得代审）；
- 列表与详情**永不**返回密文、会话密钥、对象名或明文；
- `/pending`、`/pending/export` 必须先于 `/{report_id}` 注册，否则 "pending" 会被动态段吃掉。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import ok
from app.models.users import User
from app.schemas.common import Envelope, PageResult
from app.schemas.finance import (
    DecryptData,
    DecryptRequest,
    FinanceDetailData,
    FinancePendingItem,
    FinancePendingQuery,
    ReviewData,
    ReviewRequest,
    VerifyData,
)
from app.services import finance_service, preview_service, review_service, verify_service
from app.services.credential_store import CredentialStore, get_credential_store
from app.services.storage import StorageService, get_storage
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


@router.post(
    "/reports/{report_id}/verify",
    response_model=Envelope[VerifyData],
    summary="验签（七步；财务私钥口令走环境变量；失败 409 不返回明文）",
)
def verify_finance_report(
    report_id: int,
    user: User = _REVIEWER,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    store: CredentialStore = Depends(get_credential_store),
    storage: StorageService = Depends(get_storage),
) -> dict:
    return ok(
        verify_service.verify_report(
            db, user, report_id, settings=settings, store=store, storage=storage
        )
    )


@router.post(
    "/reports/{report_id}/decrypt",
    response_model=Envelope[DecryptData],
    summary="解密到临时预览（verify_token 换取 preview_token，30min）",
)
def decrypt_finance_report(
    report_id: int,
    payload: DecryptRequest,
    user: User = _REVIEWER,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    store: CredentialStore = Depends(get_credential_store),
    storage: StorageService = Depends(get_storage),
) -> dict:
    return ok(
        preview_service.decrypt_report(
            db,
            user,
            report_id,
            payload.verify_token,
            settings=settings,
            store=store,
            storage=storage,
        )
    )


@router.get(
    "/preview/{preview_token}",
    response_class=Response,
    summary="读取预览（解密后的 report JSON；过期 410）",
)
def get_preview(
    preview_token: str,
    user: User = _REVIEWER,
    settings: Settings = Depends(get_settings),
    store: CredentialStore = Depends(get_credential_store),
    storage: StorageService = Depends(get_storage),
) -> Response:
    content, _meta = preview_service.read_preview(
        store, storage, preview_token, settings=settings
    )
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/preview/{preview_token}/files/{file_id}",
    response_class=Response,
    summary="读取预览影像（明文，30min 内可重复读取）",
)
def get_preview_file(
    preview_token: str,
    file_id: int,
    page: int = Query(1, ge=1, le=20),
    user: User = _REVIEWER,
    settings: Settings = Depends(get_settings),
    store: CredentialStore = Depends(get_credential_store),
    storage: StorageService = Depends(get_storage),
) -> Response:
    content, mime = preview_service.read_preview_file(
        store, storage, preview_token, file_id, settings=settings, page=page
    )
    return Response(
        content=content,
        media_type=mime,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/reports/{report_id}/review",
    response_model=Envelope[ReviewData],
    summary="审核决策（approve 需一次性 verify_token；reject 需意见）",
)
def review_finance_report(
    report_id: int,
    payload: ReviewRequest,
    user: User = _REVIEWER,
    db: Session = Depends(get_db),
    store: CredentialStore = Depends(get_credential_store),
) -> dict:
    return ok(
        review_service.decide_report(db, user, report_id, payload, store=store)
    )