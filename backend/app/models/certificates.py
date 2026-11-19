"""certificates — 自建 CA 签发的证书登记簿（SCHEMA §2.2）。

能力边界（方案 6.5.6）：只做"登记 + 指纹比对 + 有效期 + 未吊销"，
不做证书链验证、不做 CRL/OCSP。`owner_id` 是多态引用，不建外键。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, hex64_check
from app.models.enums import CertOwnerType, KeyAlgo, enum_check


class Certificate(Base):
    __tablename__ = "certificates"

    __table_args__ = (
        UniqueConstraint("issuer_dn", "serial_no", name="uq_certificates_issuer_serial"),
        UniqueConstraint("fingerprint_sha256", name="uq_certificates_fingerprint"),
        # 多态引用：server 无归属人，user 必须有
        CheckConstraint(
            "(owner_type = 'server' AND owner_id IS NULL)"
            " OR (owner_type = 'user' AND owner_id IS NOT NULL)",
            name="ck_certificates_owner_pair",
        ),
        CheckConstraint("not_after > not_before", name="ck_certificates_validity"),
        # 未吊销时不得有吊销时间（吊销后是否回填时间由业务决定）
        CheckConstraint("revoked OR revoked_at IS NULL", name="ck_certificates_revoked_at"),
        hex64_check("certificates", "fingerprint_sha256"),
        enum_check("certificates", "owner_type", CertOwnerType),
        enum_check("certificates", "key_algo", KeyAlgo),
        Index("ix_certificates_owner_type", "owner_type"),
        Index("ix_certificates_owner_id", "owner_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    owner_type: Mapped[str] = mapped_column(Text, nullable=False)
    # 多态外键：owner_type='user' 时为 users.id，'server' 时为 NULL；不建外键
    owner_id: Mapped[int | None] = mapped_column(BigInteger)
    subject_dn: Mapped[str] = mapped_column(Text, nullable=False)
    # X.509 序列号是十六进制字符串（可达 20 字节，不能用 BIGINT）
    serial_no: Mapped[str] = mapped_column(Text, nullable=False)
    issuer_dn: Mapped[str] = mapped_column(Text, nullable=False)
    key_algo: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'SM2'"))
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    cert_pem: Mapped[str] = mapped_column(Text, nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fingerprint_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )