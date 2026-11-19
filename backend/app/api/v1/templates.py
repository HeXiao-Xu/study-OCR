"""模板与规则接口（API.md §3）。

写操作仅 admin；reviewer 只读；submitter 403。`/test` 是即时计算：不建票、不投任务、不落 MinIO。
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import ok
from app.models.users import User
from app.schemas.common import Envelope, PageResult
from app.schemas.template import (
    TemplateCreate,
    TemplateDetailData,
    TemplateListQuery,
    TemplateOut,
    TemplateTestData,
    TemplateUpdate,
)
from app.services import template_service

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=Envelope[PageResult[TemplateOut]], summary="模板列表（默认只列 active）")
def list_templates(
    query: Annotated[TemplateListQuery, Query()],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(template_service.list_templates(db, user, query))


@router.get("/{template_id}", response_model=Envelope[TemplateDetailData], summary="模板详情（含规格与规则）")
def get_template(
    template_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(template_service.get_template(db, user, template_id))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[TemplateOut],
    summary="创建模板（version=1）",
)
def create_template(
    payload: TemplateCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(template_service.create_template(db, user, payload))


@router.put(
    "/{template_id}",
    response_model=Envelope[TemplateOut],
    summary="升版本（生成 version+1 新行，旧行保留）",
)
def update_template(
    template_id: int,
    payload: TemplateUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(template_service.update_template(db, user, template_id, payload))


@router.post(
    "/{template_id}/test",
    response_model=Envelope[TemplateTestData],
    summary="用该模板试跑一张图（不落库）",
)
async def test_template(
    template_id: int,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    data = await asyncio.to_thread(
        template_service.test_template, db, user, template_id, file, settings
    )
    return ok(data)