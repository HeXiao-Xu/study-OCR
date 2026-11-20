"""batch upload（S3-8）

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20

批量上传需要两列（D-067）：
- `invoices.batch_id`：批次弱聚合（批次不落表、不建外键），按批查询要索引；
- `ocr_jobs.dispatched`：false = 服务端排队中（单用户并发 ≤N），true = 已投递 ARQ（含排队/运行）。
  历史行默认 true，保持"已入队"语义不变。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("batch_id", sa.Uuid(), nullable=True))
    op.create_index("ix_invoices_batch_id", "invoices", ["batch_id"])
    op.add_column(
        "ocr_jobs",
        sa.Column(
            "dispatched", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )


def downgrade() -> None:
    op.drop_column("ocr_jobs", "dispatched")
    op.drop_index("ix_invoices_batch_id", table_name="invoices")
    op.drop_column("invoices", "batch_id")
