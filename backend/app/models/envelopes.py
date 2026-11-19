"""envelopes — 数字信封（SCHEMA §5.4）。

form_id 为空即"文件级信封"（同时挂在 report_forms 与 invoice_files 两侧）。
wrapped_key_b64 非空：禁止"只加密了数据、没封会话密钥"的半成品记录。
ciphertext_b64 与 ciphertext_storage_key 至少有一个非空。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, hex64_check
from app.models.enums import CipherAlgo, EnvelopeKeyAlgo, enum_check


class Envelope(Base):
    __tablename__ = "envelopes"

    __table_args__ = (
        # 一个文件最多一个信封（文件级）
        Index(
            "uq_envelopes_file",
            "file_id",
            unique=True,
            postgresql_where=text("file_id IS NOT NULL"),
        ),
        # 一张报销单最多一个载荷信封（报销单级）
        Index(
            "uq_envelopes_form",
            "form_id",
            unique=True,
            postgresql_where=text("form_id IS NOT NULL"),
        ),
        CheckConstraint("wrapped_key_b64 <> ''", name="ck_envelopes_wrapped_key"),
        CheckConstraint(
            "ciphertext_b64 IS NOT NULL OR ciphertext_storage_key IS NOT NULL",
            name="ck_envelopes_ciphertext",
        ),
        hex64_check("envelopes", "payload_sha256"),
        enum_check("envelopes", "key_algo", EnvelopeKeyAlgo),
        enum_check("envelopes", "cipher_algo", CipherAlgo),
        Index("ix_envelopes_form_id", "form_id"),
        Index("ix_envelopes_file_id", "file_id"),
        Index("ix_envelopes_recipient_cert_id", "recipient_cert_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    form_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("report_forms.id", name="fk_envelopes_form_id")
    )
    file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("invoice_files.id", name="fk_envelopes_file_id")
    )
    key_algo: Mapped[str] = mapped_column(Text, nullable=False)
    cipher_algo: Mapped[str] = mapped_column(Text, nullable=False)
    # SM2 包裹后的 CEK（C1C3C2，C1 不带 0x04 前缀）
    wrapped_key_b64: Mapped[str] = mapped_column(Text, nullable=False)
    iv_b64: Mapped[str] = mapped_column(Text, nullable=False)
    # HMAC-SM3 覆盖 header‖IV‖密文‖收方证书指纹
    mac_b64: Mapped[str] = mapped_column(Text, nullable=False)
    # 小体积载荷密文（报销单 JSON）
    ciphertext_b64: Mapped[str | None] = mapped_column(Text)
    # 大体积影像密文在 MinIO 的对象名
    ciphertext_storage_key: Mapped[str | None] = mapped_column(Text)
    # 明文载荷的 SHA-256，拆封后校验
    payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    # 收方证书参与 MAC 覆盖范围，防"信封被换收方"
    recipient_cert_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("certificates.id", name="fk_envelopes_recipient_cert_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )