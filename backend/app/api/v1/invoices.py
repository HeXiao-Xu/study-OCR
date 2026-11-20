"""票据接口（API.md §2）：列表 / 详情 / 软删 / 字段修改 / 识别进度 / 局部重识别。

注意：
- 建票走 `POST /invoices/upload`（S2-6），本模块没有 `POST /invoices`；
- 字段修改用契约的 `PATCH /invoices/{id}/fields`（S3-6 复用其行为），没有 `PATCH /invoices/{id}`；
- 行级隔离由 `services/repository.py` 统一负责，路由不写 where。
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_task_queue
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import ServiceUnavailableError, ok
from app.models.users import User
from app.schemas.common import Envelope, PageResult
from app.schemas.invoice import (
    InvoiceDetailData,
    InvoiceFieldsUpdateData,
    InvoiceFieldsUpdateRequest,
    InvoiceListQuery,
    InvoiceOut,
    InvoiceFieldsData,
    InvoiceLinesData,
    OcrStatusData,
    ReocrData,
    ReocrRequest,
)
from app.services import (
    image_service,
    invoice_service,
    job_service,
    line_service,
    ocr_status_service,
    reocr_service,
)
from app.services.storage import StorageService, get_storage
from app.tasks.queue import OcrTaskQueue

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=Envelope[PageResult[InvoiceOut]], summary="票据列表（分页/筛选）")
def list_invoices(
    query: Annotated[InvoiceListQuery, Query()],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(invoice_service.list_invoices(db, user, query))


@router.get("/{invoice_id}", response_model=Envelope[InvoiceDetailData], summary="票据详情")
def get_invoice(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(invoice_service.get_invoice_detail(db, user, invoice_id))


@router.get(
    "/{invoice_id}/lines",
    response_model=Envelope[InvoiceLinesData],
    summary="行框（原图坐标，不含文本）",
)
def get_invoice_lines(
    invoice_id: int,
    page: int = Query(1, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
) -> dict:
    return ok(
        line_service.get_invoice_lines(
            db, user, invoice_id, page=page, settings=settings, storage=storage
        )
    )


@router.get(
    "/{invoice_id}/fields",
    response_model=Envelope[InvoiceFieldsData],
    summary="字段级识别结果（原文/归一值/置信度/冲突）",
)
def get_invoice_fields(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(invoice_service.get_invoice_fields(db, user, invoice_id))


@router.get(
    "/{invoice_id}/ocr-status",
    response_model=Envelope[OcrStatusData],
    summary="识别进度（轮询：阶段/进度/起止时间）",
)
def get_invoice_ocr_status(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(ocr_status_service.get_ocr_status(db, user, invoice_id))


@router.post(
    "/{invoice_id}/reocr",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Envelope[ReocrData],
    summary="局部二次识别（按字段裁剪放大重识别，异步）",
)
async def reocr_invoice(
    invoice_id: int,
    payload: ReocrRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    queue: OcrTaskQueue = Depends(get_task_queue),
) -> dict:
    data = await asyncio.to_thread(
        reocr_service.submit_reocr, db, user, invoice_id, payload
    )
    try:
        await queue.enqueue_reocr(data.job_id)
    except Exception:
        # 已经落库了：入队失败必须把作业置 failed，绝不返回"假成功"（AGENTS §10.5）
        await asyncio.to_thread(job_service.mark_enqueue_failed, data.job_id, db=db)
        raise ServiceUnavailableError("任务队列不可用") from None
    return ok(data, "accepted")


@router.get(
    "/{invoice_id}/image",
    summary="票据影像（原图/预处理图；PDF 按 250 DPI 渲染）",
    response_class=Response,
)
def get_invoice_image(
    invoice_id: int,
    page: int = Query(1, ge=1),
    variant: Literal["original", "preprocessed"] = "original",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
) -> Response:
    rendered = image_service.get_invoice_image(
        db, user, invoice_id, page=page, variant=variant, settings=settings, storage=storage
    )
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={
            "ETag": f'"{rendered.etag}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.patch(
    "/{invoice_id}/fields",
    response_model=Envelope[InvoiceFieldsUpdateData],
    summary="批量修改字段（乐观锁 + 权限矩阵）",
)
def update_invoice_fields(
    invoice_id: int,
    payload: InvoiceFieldsUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(invoice_service.update_fields(db, user, invoice_id, payload))


@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="软删票据（status=void，幂等）",
)
def delete_invoice(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    invoice_service.soft_delete(db, user, invoice_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)