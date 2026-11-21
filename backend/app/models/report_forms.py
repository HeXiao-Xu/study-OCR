"""report_forms — 报销单主体 + 签名域快照 + 状态机（SCHEMA §5.1）。

canonical_json 必须是 TEXT：它存"签名当时的字节"，JSONB 会重排键序、
归一数字格式，验签时重算摘要必然不一致（定稿清单第 15 项）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, hex64_check
from app.models.enums import DigestAlgo, ReportStatus, enum_check


class ReportForm(Base):
    __tablename__ = "report_forms"

    __table_args__ = (
        UniqueConstraint("report_no", name="uq_report_forms_report_no"),
        UniqueConstraint("idempotency_key", name="uq_report_forms_idempotency_key"),
        # S5-6：归档号唯一；归档号与归档时间同进同出；archived 必须有归档号（库兜底，不只靠服务）
        UniqueConstraint("archive_no", name="uq_report_forms_archive_no"),
        CheckConstraint(
            "(archive_no IS NULL) = (archive_at IS NULL)",
            name="ck_report_forms_archive_pair",
        ),
        CheckConstraint(
            "status <> 'archived' OR archive_no IS NOT NULL",
            name="ck_report_forms_archived_fields",
        ),
        CheckConstraint("total_amount >= 0", name="ck_report_forms_total_amount"),
        CheckConstraint("item_count >= 0", name="ck_report_forms_item_count"),
        hex64_check("report_forms", "digest_hex"),
        enum_check("report_forms", "status", ReportStatus),
        enum_check("report_forms", "digest_algo", DigestAlgo),
        Index("ix_report_forms_applicant_created", "applicant_id", text("created_at DESC")),
        Index("ix_report_forms_status", "status"),
        Index("ix_report_forms_archive_at", "archive_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # 业务单号 RPT-YYYYMM-序号：对外展示与审计引用都用它
    report_no: Mapped[str] = mapped_column(Text, nullable=False)
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_report_forms_applicant_id"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    # 明细汇总，服务端重算，不信客户端
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    # 签名当时的规范化载荷原样字符串（方案 6.5.1 第 7 条）
    canonical_json: Mapped[str | None] = mapped_column(Text)
    digest_algo: Mapped[str | None] = mapped_column(Text, server_default=text("'SM3'"))
    # 只用于展示与快速比对，不是验签依据
    digest_hex: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 重复点击由数据库兜底，不靠前端防抖
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    # 乐观锁计数器（与 signatures.version 是两回事）
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # S5-6：归档不可逆；两列同进同出，归档号 AR-YYYY-NNNN
    archive_no: Mapped[str | None] = mapped_column(Text)
    archive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=func.now()
    )