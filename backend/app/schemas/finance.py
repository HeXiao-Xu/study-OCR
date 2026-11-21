"""财务端出入参（API.md §5，S5-3）。

红线：列表与详情**永不**出现 `ciphertext_b64` / `wrapped_key_b64` / `ciphertext_storage_key` /
私钥 / 口令 / `storage_path`；`verify_status` 是派生字段（由 `signatures` 行得出，D-086）。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer, model_validator

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
    # 乐观锁版本（S5-5 决策要带）；ReportOut 本身不含 version，故在财务详情里单独给
    report_version: int = 0
    signature: SignatureOut | None = None
    envelope: EnvelopeOut | None = None
    verify_status: VerifyStatus = "unverified"
    attachments: list[FinanceAttachmentOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 验签 / 解密预览（S5-4）
# --------------------------------------------------------------------------- #
class VerifyStep(BaseModel):
    """七步校验的一步（顺序见 API.md §5）。"""

    step: str
    ok: bool


class VerifyData(BaseModel):
    """`POST /finance/reports/{id}/verify` 出参（API.md §5）：不返回任何明文。"""

    report_id: int
    verified: bool
    steps: list[VerifyStep]
    cert_fp: str | None = None
    payload_sha256_match: bool
    verified_at: datetime
    verify_token: str
    verify_token_expires_in: int


class DecryptRequest(BaseModel):
    """解密预览入参：必须是 verify 通过后签发的凭据。"""

    verify_token: str = Field(min_length=8, max_length=128)


class DecryptData(BaseModel):
    """解密预览出参：临时凭据 + 字段预览；明文影像走 `GET /finance/preview/{token}/files/{id}`。"""

    preview_token: str
    expires_at: datetime
    ttl_seconds: int
    content_type: str
    size: int
    fields_preview: dict[str, Any]


# --------------------------------------------------------------------------- #
# 审核决策（S5-5）
# --------------------------------------------------------------------------- #
class ReviewRequest(BaseModel):
    """审核决策入参（API.md §5）：approve 必须有 verify_token，reject 必须有 comment。"""

    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=500)
    version: int = Field(ge=0, description="乐观锁（report_forms.version）")
    verify_token: str | None = Field(default=None, min_length=8, max_length=128)

    @model_validator(mode="after")
    def _require_conditionals(self) -> "ReviewRequest":
        if self.decision == "reject" and not (self.comment or "").strip():
            raise ValueError("驳回必须填写意见（1–500 字符）")
        if self.decision == "approve" and not self.verify_token:
            raise ValueError("通过必须先验签（缺少 verify_token）")
        return self


class ReviewData(BaseModel):
    """审核决策出参（API.md §5）。"""

    report_id: int
    report_no: str
    status: str
    decision: str
    reviewed_at: datetime