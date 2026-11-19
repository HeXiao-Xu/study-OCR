"""ocr_lines — OCR 文本行与坐标（SCHEMA §3.4）。

box_json 是**四点坐标** [[x,y],...]（透视校正后文本框可能是斜的），
字段层再取外接矩形聚合成 [x1,y1,x2,y2]。
(invoice_id, page_no, line_no) 唯一：重跑同页是覆盖不是追加。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text as sa_text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, probability_check


class OcrLine(Base):
    __tablename__ = "ocr_lines"

    __table_args__ = (
        UniqueConstraint(
            "invoice_id", "page_no", "line_no", name="uq_ocr_lines_invoice_page_line"
        ),
        probability_check("ocr_lines", "score"),
        Index("ix_ocr_lines_job_id", "job_id"),
        Index("ix_ocr_lines_block_id", "block_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("invoices.id", name="fk_ocr_lines_invoice_id"), nullable=False
    )
    # 产生该行的作业；重跑后可追溯是哪一次识别
    job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ocr_jobs.id", name="fk_ocr_lines_job_id")
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa_text("1"))
    # 页内行号，从上到下从 0 开始
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    # 四点坐标（原图坐标系，顺时针）
    box_json: Mapped[list[Any] | None] = mapped_column(postgresql.JSONB)
    # 版面块标识，格式 b{页码}-{块序号}，如 b1-3（PaddleOCR 无稳定 block id，自行编号）
    block_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )