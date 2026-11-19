"""field_rules — 字段校验规则库（SCHEMA §4.2）。

template_id 为空即"通用规则"；priority 数值小者先，同分按 id 升序（D-014）。
pattern 是正则源码字符串，写入时由服务层 re.compile 校验，非法直接 422。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import FieldValidator, enum_check


class FieldRule(Base):
    __tablename__ = "field_rules"

    __table_args__ = (
        enum_check("field_rules", "validator", FieldValidator),
        Index("ix_field_rules_field_name", "field_name"),
        Index("ix_field_rules_template_id", "template_id"),
        Index("ix_field_rules_priority", "priority"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL = 通用规则
    template_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ticket_templates.id", name="fk_field_rules_template_id")
    )
    pattern: Mapped[str | None] = mapped_column(Text)
    validator: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=func.now()
    )