"""ticket type: add vat_digital

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19

S2-8 拍板（D-046）：S1 已有数电票 layout 模板（D-023 的主流形态），但契约 §9.6 缺 `vat_digital`。
本迁移只放开两个 CHECK（ticket_templates.type_code / invoices.invoice_type），**不写数据**。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = ("vat_general", "train_ticket", "flight_itinerary", "taxi_ticket", "unknown")
_NEW_VALUES = (
    "vat_general",
    "vat_digital",
    "train_ticket",
    "flight_itinerary",
    "taxi_ticket",
    "unknown",
)

_CHECKS = (
    ("ck_ticket_templates_type_code", "ticket_templates", "type_code"),
    ("ck_invoices_invoice_type", "invoices", "invoice_type"),
)


def _sql(column: str, values: Sequence[str]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({allowed})"


def upgrade() -> None:
    for name, table, column in _CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _sql(column, _NEW_VALUES))


def downgrade() -> None:
    # 已有 vat_digital 数据时不允许静默回退（会把合法票据变成脏值）
    bind = op.get_bind()
    used = bind.execute(
        sa.text(
            "select count(*) from ticket_templates where type_code = 'vat_digital'"
        )
    ).scalar_one() + bind.execute(
        sa.text("select count(*) from invoices where invoice_type = 'vat_digital'")
    ).scalar_one()
    if used:
        raise RuntimeError("已有 vat_digital 数据，禁止 downgrade（会破坏 CHECK）")

    for name, table, column in _CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _sql(column, _OLD_VALUES))