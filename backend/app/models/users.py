"""users — 账号与角色（SCHEMA §2.1）。

只有三种角色：submitter / reviewer / admin。财务服务器不是用户，
而是 certificates.owner_type='server' 的证书主体。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import UserRole, UserStatus, enum_check


class User(Base):
    __tablename__ = "users"

    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint("length(username) BETWEEN 3 AND 64", name="ck_users_username"),
        enum_check("users", "role", UserRole),
        enum_check("users", "status", UserStatus),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # 登录名全局唯一且不可改（签名与审计靠它追溯）
    username: Mapped[str] = mapped_column(Text, nullable=False)
    # Argon2id；SSO 场景可为空
    password_hash: Mapped[str | None] = mapped_column(Text)
    real_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'submitter'"))
    dept: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # 应用层 onupdate，不用数据库触发器（SCHEMA §0.2）
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=func.now()
    )