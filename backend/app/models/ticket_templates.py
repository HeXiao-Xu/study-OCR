"""ticket_templates — 票种模板与字段规格（SCHEMA §4.1）。

同一票种允许多版本共存，但只有一版 active=true（部分唯一索引兜底）。
field_specs_json 的结构就是 ocr/templates/*.yaml 里 fields: 的子树。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import TicketType, enum_check


class TicketTemplate(Base):
    __tablename__ = "ticket_templates"

    __table_args__ = (
        UniqueConstraint("type_code", "version", name="uq_ticket_templates_type_version"),
        # 同一票种只能有一版生效模板（改版先停旧再启新，同事务两次 flush）
        Index(
            "uq_ticket_templates_active_type",
            "type_code",
            unique=True,
            postgresql_where=text("active"),
        ),
        # 按字段名查"哪个模板定义了该字段的定位策略"
        Index("ix_templates_specs", "field_specs_json", postgresql_using="gin"),
        enum_check("ticket_templates", "type_code", TicketType),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    type_code: Mapped[str] = mapped_column(Text, nullable=False)
    type_name: Mapped[str] = mapped_column(Text, nullable=False)
    match_keywords: Mapped[list[str] | None] = mapped_column(postgresql.ARRAY(Text))
    # NOT NULL：模板没有字段规格就没有意义，契约不给默认值（spec §6）
    field_specs_json: Mapped[dict[str, Any]] = mapped_column(postgresql.JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=func.now()
    )