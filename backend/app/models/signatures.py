"""signatures — 签名记录（SCHEMA §5.3）。

每张报销单至多一条 active 签名（部分唯一索引兜底）；历史签名保留为 void。
verify_result='ok' 与 verified=true 必须同真同假，避免"显示已验签但结果是失败"。
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

from app.models.base import Base
from app.models.enums import (
    DigestAlgo,
    SignatureAlgo,
    SignatureStatus,
    VerifyResult,
    enum_check,
)


class Signature(Base):
    __tablename__ = "signatures"

    __table_args__ = (
        UniqueConstraint("form_id", "version", name="uq_signatures_form_version"),
        # 每单只能有一条有效签名（重新签名须先作废旧签名）
        Index(
            "uq_signatures_form_active",
            "form_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint("digest_hex ~ '^[0-9a-f]{64}$'", name="ck_signatures_digest_hex"),
        # 未验签时 verify_result 为 NULL、verified=false；CHECK 遇 NULL 不违反
        CheckConstraint(
            "verified = (verify_result = 'ok')", name="ck_signatures_verified_consistency"
        ),
        enum_check("signatures", "algo", SignatureAlgo),
        enum_check("signatures", "digest_algo", DigestAlgo),
        enum_check("signatures", "status", SignatureStatus),
        enum_check("signatures", "verify_result", VerifyResult),
        Index("ix_signatures_form_id", "form_id"),
        Index("ix_signatures_signer_id", "signer_id"),
        Index("ix_signatures_cert_id", "cert_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    form_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("report_forms.id", name="fk_signatures_form_id"), nullable=False
    )
    # 职责分离：提交人签提交、审核人签意见
    signer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_signatures_signer_id"), nullable=False
    )
    algo: Mapped[str] = mapped_column(Text, nullable=False)
    digest_algo: Mapped[str] = mapped_column(Text, nullable=False)
    digest_hex: Mapped[str] = mapped_column(Text, nullable=False)
    # base64（SM2 签名约 70–72 字节）
    signature_b64: Mapped[str] = mapped_column(Text, nullable=False)
    # 验签时校验证书公钥与签名公钥一致
    cert_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("certificates.id", name="fk_signatures_cert_id"), nullable=False
    )
    # 第几次签名（驳回重提 +1，历史行保留）
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    verify_result: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 系统自动验签为 NULL
    verifier_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_signatures_verifier_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )