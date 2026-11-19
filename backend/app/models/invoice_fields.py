"""invoice_fields — 字段级抽取结果、置信度与冲突（SCHEMA §3.3）。

一个字段一行（upsert 不是追加）；冲突时两个值都留：
识别值在 field_value，校验值在 conflict_value，**不静默改写**。
bbox_json 是 [x1,y1,x2,y2] 外接框（原图坐标系），前端画框只认这一种格式。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, probability_check
from app.models.enums import FieldSource, enum_check


class InvoiceField(Base):
    __tablename__ = "invoice_fields"

    __table_args__ = (
        UniqueConstraint("invoice_id", "field_name", name="uq_invoice_fields_invoice_field"),
        probability_check("invoice_fields", "confidence"),
        enum_check("invoice_fields", "source", FieldSource),
        Index("ix_invoice_fields_source", "source"),
        Index("ix_invoice_fields_need_review", "need_review"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("invoices.id", name="fk_invoice_fields_invoice_id"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    # 票面原文形态，如 ¥1,234.56
    field_value: Mapped[str | None] = mapped_column(Text)
    # 归一值：金额两位小数字符串 / 日期 YYYY-MM-DD
    normalized_value: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    # 两点外接框 [x1,y1,x2,y2]
    bbox_json: Mapped[list[Any] | None] = mapped_column(postgresql.JSONB)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ocr'"))
    # 回查 ocr_lines 的键是 (invoice_id, page_no, line_no)，缺页码会歧义
    source_page_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    source_line_no: Mapped[int | None] = mapped_column(Integer)
    rule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("field_rules.id", name="fk_invoice_fields_rule_id")
    )
    need_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # 冲突时的"校验值 B"
    conflict_value: Mapped[str | None] = mapped_column(Text)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_invoice_fields_updated_by")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )