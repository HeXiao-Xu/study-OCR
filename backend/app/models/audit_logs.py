"""audit_logs — 追加写审计日志 + 哈希链（SCHEMA §6.1）。

只 INSERT，不 UPDATE / 不 DELETE；entry_hash 由 HMAC-SM3 链式覆盖 prev_hash。
哈希链防"改了不被发现"，不防"被读"，也不是区块链（论文口径）。
并发写入在 S4-7 用 pg_advisory_xact_lock 串行化（D-015）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

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
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import AuditAction, AuditActorRole, AuditTargetType, enum_check


class AuditLog(Base):
    __tablename__ = "audit_logs"

    __table_args__ = (
        UniqueConstraint("entry_hash", name="uq_audit_logs_entry_hash"),
        CheckConstraint("prev_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_logs_prev_hash"),
        CheckConstraint("entry_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_logs_entry_hash"),
        enum_check("audit_logs", "actor_role", AuditActorRole),
        enum_check("audit_logs", "action", AuditAction),
        enum_check("audit_logs", "target_type", AuditTargetType),
        Index("ix_audit_logs_trace_id", "trace_id"),
        Index("ix_audit_logs_actor_id", "actor_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_target", "target_type", "target_id"),
        Index("ix_audit_logs_created", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 系统任务 / 匿名失败请求为 NULL
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_audit_logs_actor_id")
    )
    # 操作时角色快照（含 system）
    actor_role: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    # 多态引用，不建外键（目标可能已作废或跨表）
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    # 动作细节，必须脱敏；禁止任何密钥材料
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)
    # 上一条 entry_hash；创世行为 64 个 0
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False)
    entry_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )