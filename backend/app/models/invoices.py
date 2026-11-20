"""invoices — 票据业务主体与识别/确认状态（SCHEMA §3.2）。

关键不变式：
- `(invoice_code, invoice_no)` 在非作废票据内唯一（同一张票不能报两次），作废后释放；
- `file_id` 唯一（一个文件最多对应一张票）；
- 票据级状态只有 5 个值，报销单状态不混进来（D-011）。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, probability_check
from app.models.enums import (
    ExtractStatus,
    InvoiceStatus,
    OcrStatus,
    QualityFlag,
    TicketType,
    enum_check,
)


class Invoice(Base):
    __tablename__ = "invoices"

    __table_args__ = (
        UniqueConstraint("file_id", name="uq_invoices_file_id"),
        # 票据级查重；作废（void）后释放，允许重新提交（方案 7.5）
        Index(
            "uq_invoices_code_no",
            "invoice_code",
            "invoice_no",
            unique=True,
            postgresql_where=text("status <> 'void'"),
        ),
        CheckConstraint("total_amount >= 0", name="ck_invoices_total_amount"),
        probability_check("invoices", "overall_confidence"),
        enum_check("invoices", "invoice_type", TicketType),
        enum_check("invoices", "ocr_status", OcrStatus),
        enum_check("invoices", "extract_status", ExtractStatus),
        enum_check("invoices", "status", InvoiceStatus),
        enum_check("invoices", "quality_flag", QualityFlag),
        Index("ix_invoices_uploader_created", "uploader_id", text("created_at DESC")),
        Index("ix_invoices_template_id", "template_id"),
        Index("ix_invoices_ocr_status", "ocr_status"),
        Index("ix_invoices_extract_status", "extract_status"),
        Index("ix_invoices_duplicate_of", "duplicate_of"),
        Index("ix_invoices_batch_id", "batch_id"),
        # 待确认队列 / 我的草稿：两条部分索引（SCHEMA 定稿清单第 13 项）
        Index(
            "ix_invoices_need_confirm",
            "uploader_id",
            postgresql_where=text("extract_status = 'need_confirm'"),
        ),
        Index("ix_invoices_draft", "uploader_id", postgresql_where=text("status = 'draft'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uploader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_invoices_uploader_id"), nullable=False
    )
    template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ticket_templates.id", name="fk_invoices_template_id")
    )
    # 冗余存一份票种代码，列表页免 JOIN
    invoice_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    invoice_code: Mapped[str | None] = mapped_column(Text)
    invoice_no: Mapped[str | None] = mapped_column(Text)
    # 业务日期（票面开票日期），明确用 DATE，不改 TIMESTAMPTZ（SCHEMA §0.2）
    invoice_date: Mapped[date | None] = mapped_column(Date)
    buyer_name: Mapped[str | None] = mapped_column(Text)
    buyer_tax_no: Mapped[str | None] = mapped_column(Text)
    seller_name: Mapped[str | None] = mapped_column(Text)
    seller_tax_no: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    check_code: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("invoice_files.id", name="fk_invoices_file_id"), nullable=False
    )
    ocr_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    extract_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'raw'"))
    overall_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    quality_flag: Mapped[str | None] = mapped_column(Text)
    # 疑似重复的既有票据：软提示不硬拦（方案 6.7 语义级）
    duplicate_of: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("invoices.id", name="fk_invoices_duplicate_of")
    )
    # 批量上传的弱聚合（S3-8）：批次本身不落表，不为它建外键
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    # 乐观锁计数器（与 signatures.version 无关）
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=func.now()
    )