"""report_items — 报销单明细（SCHEMA §5.2）。

(form_id, invoice_id) 唯一：同一张票在一张单里只能出现一次，
否则合计与签名域都可被重复计算。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReportItem(Base):
    __tablename__ = "report_items"

    __table_args__ = (
        UniqueConstraint("form_id", "invoice_id", name="uq_report_items_form_invoice"),
        CheckConstraint("amount >= 0", name="ck_report_items_amount"),
        Index("ix_report_items_invoice_id", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    form_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("report_forms.id", name="fk_report_items_form_id"), nullable=False
    )
    invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("invoices.id", name="fk_report_items_invoice_id"), nullable=False
    )
    # 本单报销金额，允许小于票面合计（部分报销），不得为负
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )