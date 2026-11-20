"""ocr_jobs — 识别运行的作业记录（SCHEMA §3.5）。

**一次运行只有一行**（表里没有 run_id，多行无法归组）；`stage` 记当前/最后阶段，
逐阶段状态与耗时写 `metrics.stages`，重试复用该行（`retry_count` 记第几次尝试）。
ocr_jobs 是 AGENTS §10.5 "handler 不得自行 commit" 的豁免之一：
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
    # 逐阶段状态/耗时、引擎参数、警告；不含明文（SCHEMA §3.5，S3-1/D-055）
    metrics: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)
    # 本次尝试开始 + 终态结束（ocr-status 契约要真时间，D-056）
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )