"""审计接口的 Pydantic 模型（API.md §7）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AuditAction, AuditTargetType
from app.schemas.common import PageParams


class AuditLogOut(BaseModel):
    """审计行输出。`detail_json` 由写入方保证已脱敏；**永不输出审计 key**。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trace_id: str
    actor_id: int | None = None
    actor_role: str | None = None
    action: str
    target_type: str | None = None
    target_id: int | None = None
    detail_json: dict[str, Any] | None = None
    prev_hash: str
    entry_hash: str
    created_at: datetime


class AuditListQuery(PageParams):
    """`GET /admin/audit` 的筛选参数；`from`/`to` 是保留字，用别名接收。"""

    model_config = ConfigDict(populate_by_name=True)

    actor_id: int | None = Field(default=None, ge=1)
    action: AuditAction | None = None
    target_type: AuditTargetType | None = None
    target_id: int | None = Field(default=None, ge=1)
    trace_id: str | None = Field(default=None, max_length=128)
    date_from: datetime | None = Field(default=None, alias="from")
    date_to: datetime | None = Field(default=None, alias="to")


class AuditVerifyData(BaseModel):
    """`GET /admin/audit/verify-chain` 的出参（API.md §7）。"""

    ok: bool
    checked_count: int
    broken_at: int | None = None
    broken_id: int | None = None
    reason: str | None = None