"""review_logs — 财务审核记录（SCHEMA §5.5）。

审核结论不可篡改、不可删除（追加写）；同一单可多次审核（驳回后重提），
因此不加唯一约束。decrypt_ok=false 或 verify_result<>'ok' 时不允许 approve，
这条由业务层强校验（库级无法表达"基于另一列取值的动作禁令"）。
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
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import ReviewDecision, VerifyResult, enum_check


class ReviewLog(Base):
    __tablename__ = "review_logs"

    __table_args__ = (
        enum_check("review_logs", "decision", ReviewDecision),
        enum_check("review_logs", "verify_result", VerifyResult),
        Index("ix_review_logs_form_id", "form_id"),
        Index("ix_review_logs_reviewer_id", "reviewer_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    form_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("report_forms.id", name="fk_review_logs_form_id"), nullable=False
    )
    reviewer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_review_logs_reviewer_id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    # 驳回时必填，业务层校验
    comment: Mapped[str | None] = mapped_column(Text)
    verify_result: Mapped[str | None] = mapped_column(Text)
    decrypt_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )