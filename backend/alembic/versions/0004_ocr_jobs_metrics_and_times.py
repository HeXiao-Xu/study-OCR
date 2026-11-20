"""ocr_jobs 增加 metrics / started_at / finished_at

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-20

S3-1（D-055/D-056）：
- `metrics` 承载逐阶段状态与耗时（一次运行一行 + `metrics.stages`）；
- `started_at`/`finished_at` 供 `GET /invoices/{id}/ocr-status` 输出真实起止时间
  （不再用 `created_at + elapsed_ms` 反推）。
三列均可空，**不写数据**；回滚只删列，无数据丢失。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ocr_jobs", sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("ocr_jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ocr_jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("ocr_jobs", "finished_at")
    op.drop_column("ocr_jobs", "started_at")
    op.drop_column("ocr_jobs", "metrics")