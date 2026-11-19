"""上传接口（S2-6）：`POST /api/v1/invoices/upload`。

契约路径是 `/invoices/upload`（上传即建票），不是任务卡里的 `/invoices/{id}/files`（D-044）。
路由保持薄：参数校验交给 FastAPI，业务在 `services/upload_service.py`。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import ok
from app.models.users import User
from app.schemas.common import Envelope
from app.schemas.invoice import UploadData
from app.services import upload_service
from app.services.storage import StorageService, get_storage

router = APIRouter(prefix="/invoices", tags=["upload"])


@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Envelope[UploadData],
    summary="上传票据影像（魔数校验 / sha256 去重 / 落库 + 待识别作业）",
)
def upload_invoice(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
) -> dict:
    data = upload_service.upload_invoice(
        db, user, file, settings=settings, storage=storage
    )
    return ok(data, "accepted")