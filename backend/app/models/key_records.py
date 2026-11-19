"""key_records — 密钥登记簿（SCHEMA §2.3）。

**私钥本体绝不入库**：本表只登记"哪把密钥、属于谁、什么算法、密文文件在哪"。
私钥以口令加密的 PKCS#8 文件存放在 secrets/keys/，与数据库物理分离。
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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import KeyAlgo, KeyType, enum_check


class KeyRecord(Base):
    __tablename__ = "key_records"

    __table_args__ = (
        UniqueConstraint("storage_path", name="uq_key_records_storage_path"),
        # 财务服务器的信封密钥没有归属用户（D-012）
        CheckConstraint("user_id IS NOT NULL OR key_type = 'envelope'", name="ck_key_records_owner"),
        # 兜底：本列禁止存私钥内容
        CheckConstraint(
            "storage_path NOT LIKE '%PRIVATE KEY%'", name="ck_key_records_storage_path"
        ),
        enum_check("key_records", "key_type", KeyType),
        enum_check("key_records", "algo", KeyAlgo),
        Index("ix_key_records_user_id", "user_id"),
        Index("ix_key_records_cert_id", "cert_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", name="fk_key_records_user_id"))
    key_type: Mapped[str] = mapped_column(Text, nullable=False)
    cert_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("certificates.id", name="fk_key_records_cert_id"), nullable=False)
    # 只登记路径，内容只可能是口令加密后的 PKCS#8 密文
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    algo: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # 非空即失效（密钥轮换/吊销）
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))