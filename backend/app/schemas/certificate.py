"""证书管理（S4-6）：列表（admin 全部 / 本人）与吊销（admin）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageParams

__all__ = ["CertListQuery", "CertOut", "CertRevokeRequest"]


class CertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_type: str
    owner_id: int | None
    subject_dn: str
    serial_no: str
    issuer_dn: str
    key_algo: str
    not_before: datetime
    not_after: datetime
    fingerprint_sha256: str
    revoked: bool
    revoked_at: datetime | None
    created_at: datetime


class CertListQuery(PageParams):
    owner_type: Literal["user", "server"] | None = None
    revoked: bool | None = None
    sort_by: Literal["created_at", "not_after"] | None = None


class CertRevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)