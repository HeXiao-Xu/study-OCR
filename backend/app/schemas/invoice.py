"""票据接口出入参（字段以 API.md §11 与 SCHEMA 为准）。

三条红线：
- **永不返回 `storage_key`**（MinIO 对象名/路径），文件只暴露展示所需元数据；
- 金额一律输出**两位小数字符串**（`"1234.56"`），不是浮点；
- `InvoiceOut` 的字段来自 `invoices` + join 出来的 `invoice_files`，不额外编造字段。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.schemas.common import PageParams


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uploader_id: int
    template_id: int | None
    invoice_type: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime

    invoice_code: str | None
    invoice_no: str | None
    invoice_date: date | None
    buyer_name: str | None
    buyer_tax_no: str | None
    seller_name: str | None
    seller_tax_no: str | None
    amount: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    check_code: str | None
    ocr_status: str
    extract_status: str
    overall_confidence: Decimal | None
    quality_flag: str | None
    duplicate_of: int | None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None

    # 文件元数据（join 而来）
    file_id: int
    filename: str
    sha256: str
    mime: str
    size: int
    page_count: int

    @field_serializer("amount", "tax_amount", "total_amount", when_used="json")
    def _serialize_money(self, value: Decimal | None) -> str | None:
        return _money(value)


class InvoiceFieldOut(BaseModel):
    id: int
    invoice_id: int
    field_name: str
    field_value: str | None
    normalized_value: str | None
    raw_text: str | None
    confidence: Decimal | None
    bbox: list[float] | None
    source: str
    source_page_no: int
    source_line_no: int | None
    rule_id: int | None
    need_review: bool
    conflict: bool
    conflict_value: str | None
    confirmed: bool
    updated_by: int | None
    updated_at: datetime
    # 派生字段（不落库）：auto/review/manual，前端颜色与文案以它为准（S3-2）
    review_level: Literal["auto", "review", "manual"] | None = None


class LatestJobOut(BaseModel):
    job_id: int
    stage: str
    status: str
    elapsed_ms: int | None
    retry_count: int
    error: str | None


class InvoiceDetailData(BaseModel):
    invoice: InvoiceOut
    fields: list[InvoiceFieldOut]
    latest_job: LatestJobOut | None


class InvoiceListQuery(PageParams):
    """票据列表入参（API.md §2）。枚举写成 Literal，非法值由 Pydantic 直接 422。"""

    status: Literal["draft", "confirmed", "in_report", "archived", "void"] | None = None
    extract_status: Literal["raw", "need_confirm", "confirmed"] | None = None
    invoice_type: Literal[
        "vat_general",
        "vat_digital",
        "train_ticket",
        "flight_itinerary",
        "taxi_ticket",
        "unknown",
    ] | None = None
    q: str | None = Field(default=None, max_length=100, description="发票号/销售方名称子串")
    date_from: date | None = None
    date_to: date | None = None
    # S3-9 二审筛选：pending = confirmed 且未复核；reviewed = 已复核
    review_status: Literal["pending", "reviewed"] | None = None


class FieldValueIn(BaseModel):
    field_name: str = Field(min_length=1, max_length=32)
    value: str = Field(max_length=200)
    confirm: bool = False


class InvoiceFieldsUpdateRequest(BaseModel):
    version: int = Field(ge=0, description="乐观锁版本（invoices.version）")
    fields: list[FieldValueIn] = Field(min_length=1, max_length=12)
    confirm_amount: bool = Field(default=False, description="修改金额类字段时必填 true（D-017）")

    @model_validator(mode="after")
    def _reject_duplicate_fields(self) -> "InvoiceFieldsUpdateRequest":
        names = [item.field_name for item in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("fields 里不能有重复的 field_name")
        return self


class FieldChange(BaseModel):
    field_name: str
    before: str | None
    after: str


class ConflictOut(BaseModel):
    field_name: str
    reason: str
    expected: str | None = None
    actual: str | None = None


class InvoiceFieldsUpdateData(BaseModel):
    invoice_id: int
    version: int
    status: str
    updated: list[FieldChange]
    needs_confirm: list[str]
    conflicts: list[ConflictOut]


class UploadDedup(BaseModel):
    hit: bool = False
    invoice_id: int | None = None


class UploadData(BaseModel):
    """上传出参（API.md §2）：**不得出现 storage_key / 对象名**。"""

    invoice_id: int
    file_id: int
    job_id: int
    sha256: str
    status: str
    ocr_status: str
    dedup: UploadDedup


class OcrLineBoxOut(BaseModel):
    """行框（S3-5）：`bbox` 是**原图**外接框；不返回行文本，减少票面明文暴露面。"""

    line_no: int
    bbox: list[int] = Field(default_factory=list)
    score: float | None = None
    field_name: str | None = None


class InvoiceLinesData(BaseModel):
    invoice_id: int
    page_no: int
    lines: list[OcrLineBoxOut] = Field(default_factory=list)


class BatchUploadItemOut(BaseModel):
    filename: str
    status: Literal["created", "duplicate", "rejected"]
    invoice_id: int | None = None
    job_id: int | None = None
    error: str | None = None


class BatchUploadData(BaseModel):
    batch_id: UUID
    created: int
    duplicates: int
    rejected: int
    items: list[BatchUploadItemOut] = Field(default_factory=list)


class BatchInvoiceItemOut(BaseModel):
    invoice_id: int
    filename: str
    ocr_status: str
    extract_status: str
    progress: int
    error: str | None = None


class BatchSummaryData(BaseModel):
    batch_id: UUID
    total: int
    counts: dict[str, int]
    items: list[BatchInvoiceItemOut] = Field(default_factory=list)
    created_at: datetime


class BatchRetryData(BaseModel):
    invoice_id: int
    job_id: int


class DuplicateMatchOut(BaseModel):
    """重复检测匹配（S3-7）：只给最小字段，不含姓名/税号明文。"""

    invoice_id: int
    kind: Literal["invoice_no", "image", "weak"]
    invoice_code: str | None = None
    invoice_no: str | None = None
    invoice_date: date | None = None
    total_amount: Decimal | None = None
    status: str
    created_at: datetime


class DuplicateCheckData(BaseModel):
    invoice_id: int
    level: Literal["strong", "weak", "none"]
    matches: list[DuplicateMatchOut] = Field(default_factory=list)


class ConfirmRequest(BaseModel):
    """整单确认入参（API.md §2）：可选乐观锁版本。"""

    version: int | None = Field(default=None, ge=0)


class InvoiceFieldsData(BaseModel):
    """字段级识别结果（API.md §2 `GET /invoices/{id}/fields`，S3-4）。"""

    invoice_id: int
    overall_confidence: Decimal | None = None
    needs_confirm: list[str] = Field(default_factory=list)
    conflicts: list[ConflictOut] = Field(default_factory=list)
    items: list[InvoiceFieldOut] = Field(default_factory=list)


class ReocrRequest(BaseModel):
    """局部二次识别入参（API.md §2）：1–6 个字段。"""

    fields: list[str] = Field(min_length=1, max_length=6)
    version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _reject_duplicate_fields(self) -> "ReocrRequest":
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("fields 里不能有重复的 field_name")
        return self


class ReocrData(BaseModel):
    invoice_id: int
    job_id: int
    fields: list[str]


class OcrStageOut(BaseModel):
    """一个识别阶段（API.md §2 `stages` 元素）：`detect` 与 `recognize` 合并计时，故可为 null。"""

    stage: str
    status: str
    elapsed_ms: int | None = None
    note: str | None = None
    engine: str | None = None
    engine_version: str | None = None
    retry_count: int = 0
    error: str | None = None


class OcrStatusData(BaseModel):
    invoice_id: int
    ocr_status: str
    progress: int = Field(ge=0, le=100)
    stages: list[OcrStageOut] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None