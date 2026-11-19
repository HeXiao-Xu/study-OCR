"""上传接口（S2-6 / S2-7）：`POST /api/v1/invoices/upload`。

契约路径是 `/invoices/upload`（上传即建票），不是任务卡里的 `/invoices/{id}/files`（D-044）。
S2-7 起：落库提交后再入队；入队失败把 job 置 failed 并返回 503（AGENTS §10.5）。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_task_queue
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import ServiceUnavailableError, ok
from app.models.users import User
from app.schemas.common import Envelope
from app.schemas.invoice import UploadData
from app.services import job_service, upload_service
from app.services.storage import StorageService, get_storage
from app.tasks.queue import OcrTaskQueue

router = APIRouter(prefix="/invoices", tags=["upload"])


@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Envelope[UploadData],
    summary="上传票据影像（魔数校验 / sha256 去重 / 落库 + 入队）",
)
async def upload_invoice(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
    queue: OcrTaskQueue = Depends(get_task_queue),
) -> dict:
    # 读取/哈希/MinIO 都是阻塞 IO，放线程池，别卡事件循环
    data = await asyncio.to_thread(
        upload_service.upload_invoice, db, user, file, settings=settings, storage=storage
    )
    try:
        await queue.enqueue_ocr(data.job_id)
    except Exception:
        # 已经落库了：入队失败必须把作业置 failed，绝不返回"假成功"
        await asyncio.to_thread(job_service.mark_enqueue_failed, data.job_id, db=db)
        raise ServiceUnavailableError("任务队列不可用") from None
    return ok(data, "accepted")