"""S5-6 归档/检索/导出的出入参。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import PageParams

__all__ = [
    "ArchiveData",
    "ArchiveExportRequest",
    "ArchiveRequest",
    "ArchiveSearchQuery",
]


class ArchiveRequest(BaseModel):
    """归档是高风险且不可逆的动作，必须二次确认。"""

    confirm: bool = Field(default=False, description="必须为 true")


class ArchiveData(BaseModel):
    report_id: int
    report_no: str
    status: str
    archive_no: str
    archive_at: datetime


class ArchiveSearchQuery(PageParams):
    """归档检索：只查 archived；日期按归档日（Asia/Shanghai）。"""

    status: Literal["archived"] | None = Field(default=None, description="只接受 archived")
    q: str | None = Field(default=None, max_length=100, description="单号 / 申请人")
    date_from: date | None = None
    date_to: date | None = None
    amount_min: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    amount_max: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)

    @model_validator(mode="after")
    def _check_range(self) -> "ArchiveSearchQuery":
        if (
            self.amount_min is not None
            and self.amount_max is not None
            and self.amount_min > self.amount_max
        ):
            raise ValueError("金额区间非法：amount_min 不能大于 amount_max")
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("日期区间非法：date_from 不能晚于 date_to")
        return self


class ArchiveExportRequest(BaseModel):
    """口令只用于本次加密；不进日志、不回显。"""

    passphrase: str = Field(min_length=8, max_length=128)