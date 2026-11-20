"""双人复核（S3-9）

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20

提交人确认后由审核人二审（D-068）：
- `invoices.reviewed_by`：复核人（FK users，不存姓名）；
- `invoices.reviewed_at`：复核时间；非空即锁定修改，且"待复核" = confirmed 且 reviewed_at IS NULL。
不新增 status 枚举值（保住 5 值状态机）。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("reviewed_by", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_invoices_reviewed_by", "invoices", "users", ["reviewed_by"], ["id"]
    )
    op.add_column(
        "invoices", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_invoices_reviewed_at", "invoices", ["reviewed_at"])


def downgrade() -> None:
    op.drop_index("ix_invoices_reviewed_at", table_name="invoices")
    op.drop_column("invoices", "reviewed_at")
    op.drop_constraint("fk_invoices_reviewed_by", "invoices", type_="foreignkey")
    op.drop_column("invoices", "reviewed_by")
