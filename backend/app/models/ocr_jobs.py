"""ocr_jobs — 识别各阶段的作业记录（SCHEMA §3.5）。

同一 (invoice_id, stage) 的重试**追加新行**而不是改旧行，retry_count 记行内序号。
ocr_jobs 是 AGENTS §10.5 "handler 不得自行 commit" 的唯一豁免：
阶段进度必须进行中可见（前端轮询"识别到第几阶段"）。
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
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import OcrStage, OcrStatus, enum_check


class OcrJob(Base):
    __tablename__ = "ocr_jobs"

    __table_args__ = (
        CheckConstraint("retry_count >= 0", name="ck_ocr_jobs_retry_count"),
        CheckConstraint("elapsed_ms >= 0", name="ck_ocr_jobs_elapsed_ms"),
        enum_check("ocr_jobs", "stage", OcrStage),
        enum_check("ocr_jobs", "status", OcrStatus),
        Index("ix_ocr_jobs_invoice_created", "invoice_id", "created_at"),
        Index("ix_ocr_jobs_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("invoices.id", name="fk_ocr_jobs_invoice_id"), nullable=False
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    engine: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'paddleocr'"))
    # 引擎版本：论文实验复现必须记
    engine_version: Mapped[str | None] = mapped_column(Text)
    # 阶段参数（缩放比、阈值、语言等；不含明文与密钥）
    params_json: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # 只存错误码 + 短消息，不得包含明文票据内容
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )