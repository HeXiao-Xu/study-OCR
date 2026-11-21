"""报销单接口出入参（API.md §4 / §11，字段以 SCHEMA §5.1 / §5.2 为准）。

三条红线：
- 金额一律**两位小数字符串**（`"1234.56"`），内部 `Decimal`，禁止 float；
- 出参**永不**出现 `ciphertext_b64` / `wrapped_key_b64` / `ciphertext_storage_key` / 任何密钥；
- `department` 不是列：它由申请人的 `users.dept` 派生（API.md §11 备注），不落库、不接受客户端传值。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.schemas.common import PageParams


def money_text(value: Decimal | None) -> str | None:
    """金额出参统一格式化（唯一入口，避免各处 `f"{x:.2f}"` 漂移）。"""
    return None if value is None else f"{value:.2f}"


# --------------------------------------------------------------------------- #
# 明细
# --------------------------------------------------------------------------- #
class ReportItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    amount: Decimal
    remark: str | None = None

    @field_serializer("amount", when_used="json")
    def _serialize_amount(self, value: Decimal) -> str:
        return money_text(value) or "0.00"


class ReportItemCreateRequest(BaseModel):
    """加明细（API.md §4）：`invoice_id` 必须本人 `confirmed` 票据；允许部分报销。"""

    invoice_id: int = Field(gt=0)
    # max_digits/decimal_places 让三位小数在 Pydantic 层就是 42200，不用业务代码再判
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    remark: str | None = Field(default=None, max_length=200)


# --------------------------------------------------------------------------- #
# 报销单
# --------------------------------------------------------------------------- #
class ReportCreateRequest(BaseModel):
    """建单（API.md §4）：只有标题与事由；合计/申请人/部门都不接受客户端传值。"""

    title: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_no: str
    applicant_id: int
    # 派生字段：申请人 users.dept（API.md §11 备注），不是 report_forms 的列
    department: str | None = None
    title: str
    reason: str | None
    status: str
    total_amount: Decimal
    item_count: int
    digest_algo: str | None
    digest_hex: str | None
    submitted_at: datetime | None
    # S5-6：归档号/归档时间（未归档为 None）；列表与详情共用同一输出形状
    archive_no: str | None = None
    archive_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    items: list[ReportItemOut] = Field(default_factory=list)

    @field_serializer("total_amount", when_used="json")
    def _serialize_total(self, value: Decimal) -> str:
        return money_text(value) or "0.00"


# --------------------------------------------------------------------------- #
# 签名 / 信封元数据（S5-2 写入，本卡只读；见 API.md §11 的"永不返回"列）
# --------------------------------------------------------------------------- #
class SignatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    form_id: int
    signer_id: int
    cert_id: int
    verifier_id: int | None = None
    algo: str
    digest_algo: str
    digest_hex: str
    signature_b64: str
    verify_result: str | None = None
    verified: bool
    verified_at: datetime | None = None
    version: int
    status: str
    created_at: datetime


class EnvelopeOut(BaseModel):
    """信封元数据：**只有** ID/算法/IV/MAC/摘要与派生展示字段，密文与会话密钥永不出接口。"""

    id: int
    form_id: int | None
    file_id: int | None
    recipient_cert_id: int
    key_algo: str
    cipher_algo: str
    iv_b64: str
    mac_b64: str
    payload_sha256: str
    # 派生：recipient_cert_fp 由 recipient_cert_id join certificates 得到
    recipient_cert_fp: str | None = None
    # 派生：len(base64d(wrapped_key_b64))，仅供前端展示/排障，不入库
    wrapped_key_len: int | None = None
    created_at: datetime


VerifyStatus = Literal["unverified", "ok", "failed"]


class ReportDetailData(BaseModel):
    """`GET /reports/{id}` 出参（API.md §4）：S5-2 之前 signature/envelope 恒为 null。"""

    report: ReportOut
    signature: SignatureOut | None = None
    envelope: EnvelopeOut | None = None
    verify_status: VerifyStatus = "unverified"


class ReportListQuery(PageParams):
    """`GET /reports/mine` 入参：枚举与排序都写成 Literal，非法值由 Pydantic 直接 422。"""

    status: Literal[
        "draft",
        "pending_sign",
        "signed",
        "encrypted",
        "submitted",
        "approved",
        "rejected",
        "archived",
    ] | None = None
    date_from: date | None = Field(default=None, description="按创建时间（Asia/Shanghai 业务日）")
    date_to: date | None = None
    sort_by: Literal["created_at", "submitted_at"] | None = None


# --------------------------------------------------------------------------- #
# 签名 / 提交（S5-2）
# --------------------------------------------------------------------------- #
class SignRequest(BaseModel):
    """签名单据入参（API.md §4）：口令只在内存停留，不落日志、不回显。"""

    private_key_passphrase: str = Field(min_length=1, max_length=256)


class SignData(BaseModel):
    """签名单据出参：只有签名元数据，绝不含私钥/口令/CEK。"""

    signature_id: int
    digest_algo: str
    digest_hex: str
    algo: str
    signature_b64: str
    cert_id: int
    # 跨语言快速比对/排障用，不是验签依据（API.md §4）
    canonical_sha256: str
    status: str


class SubmitRequest(BaseModel):
    """提交入参（API.md §4）：幂等键必填，8–64 字符。"""

    idempotency_key: str = Field(min_length=8, max_length=64)
    version: int | None = Field(default=None, ge=0)


class SubmitData(BaseModel):
    """提交出参：只有 id 与时间，**不含任何密文与密钥**。"""

    report_id: int
    report_no: str
    status: str
    signature_id: int
    envelope_id: int
    submitted_at: datetime
    duplicated: bool