"""invoice_files — 原始票据文件元数据与去重（SCHEMA §3.1）。

文件行**不可变**（不设 updated_at）：内容一变 sha256 就变，应生成新行。
`envelope_id` 刻意不存在（D-012）：文件级信封通过 envelopes.file_id 反查。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, hex64_check


class InvoiceFile(Base):
    __tablename__ = "invoice_files"

    __table_args__ = (
        UniqueConstraint("sha256", name="uq_invoice_files_sha256"),
        UniqueConstraint("storage_key", name="uq_invoice_files_storage_key"),
        CheckConstraint("size > 0", name="ck_invoice_files_size"),
        # 第一道重复报销防线：明文哈希（SCHEMA §3.1）
        hex64_check("invoice_files", "sha256"),
        Index("ix_invoice_files_uploader_id", "uploader_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uploader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_invoice_files_uploader_id"), nullable=False
    )
    # 展示用原始文件名，禁止用于拼路径
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 明文的 SHA-256（64 位小写十六进制），用于去重与纳入签名域
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    # MinIO 对象名 invoices/{yyyy}/{mm}/{uuid}.bin，内容是 SM4-CBC 密文
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    is_cipher: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )