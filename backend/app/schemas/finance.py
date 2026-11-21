"""财务端出入参（API.md §5，S5-3）。

红线：列表与详情**永不**出现 `ciphertext_b64` / `wrapped_key_b64` / `ciphertext_storage_key` /
私钥 / 口令 / `storage_path`；`verify_status` 是派生字段（由 `signatures` 行得出，D-086）。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_serializer

from app.schemas.common import PageParams
from app.schemas.report import EnvelopeOut, ReportOut, SignatureOut, VerifyStatus, money_text


class FinancePendingQuery(PageParams):
    """`GET /finance/pending` 入参：枚举/排序写 Literal，非法值由 Pydantic 直接 422。"""

    verify_status: VerifyStatus | None = Field(default=None, description="unverified / ok / failed")
    submitted_from: date | None = Field(default=None, description="提交日（Asia/Shanghai 业务日）")
    submitted_to: date | None = None
    q: str | None = Field(default=None, max_length=100, description="单号 / 申请人")
    sort_by: Literal["submitted_at", "total_amount"] | None = None


class FinancePendingItem(BaseModel):
    """待审列表一行（API.md §5 的字段集，顺序即文档顺序）。"""

    report_id: int
    report_no: str
    applicant_id: int
    applicant_name: str
    total_amount: Decimal
    item_count: int
    submitted_at: datetime
    verify_status: VerifyStatus

    @field_serializer("total_amount", when_used="json")
    def _serialize_total(self, value: Decimal) -> str:
        return money_text(value) or "0.00"


class FinanceAttachmentOut(BaseModel):
    """附件元数据：只有展示信息与信封元数据，**不含密文/对象名**。"""

    file_id: int
    invoice_id: int
    filename: str
    mime: str
    size: int
    sha256: str
    page_count: int
    envelope: EnvelopeOut | None = None


class FinanceDetailData(BaseModel):
    """`GET /finance/{id}` 出参：单据 + 签名/信封元数据 + 附件清单（明文走 S5-4）。"""

    report: ReportOut
    applicant_name: str
    signature: SignatureOut | None = None
    envelope: EnvelopeOut | None = None
    verify_status: VerifyStatus = "unverified"
    attachments: list[FinanceAttachmentOut] = Field(default_factory=list)