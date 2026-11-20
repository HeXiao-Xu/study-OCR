"""audit action: add field_reread

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20

S3-3（D-061）：`POST /invoices/{id}/reocr` 需要 `audit_logs.action='field_reread'`。
本迁移只放开 CHECK，**不写任何审计行**——哈希链在 S4-7 实现，届时才允许 INSERT。

与 0002 同样的理由手写而不是 autogenerate：Alembic 不比对既有表上的 CHECK 约束。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = (
    "upload",
    "confirm_field",
    "sign",
    "seal",
    "submit",
    "verify",
    "decrypt",
    "review",
    "revoke_cert",
    "login_failed",
    "logout",
    "forbidden",
)
_NEW_VALUES = (*_OLD_VALUES, "field_reread")


def _check_sql(values: Sequence[str]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"action IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action", "audit_logs", _check_sql(_NEW_VALUES)
    )


def downgrade() -> None:
    # 审计日志追加写、不可删：已有 field_reread 行时回退会让这些行语义失效
    bind = op.get_bind()
    used = bind.execute(
        sa.text("select count(*) from audit_logs where action = 'field_reread'")
    ).scalar_one()
    if used:
        raise RuntimeError(
            "审计表中已有 field_reread 的行，禁止 downgrade（审计不可删）"
        )

    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action", "audit_logs", _check_sql(_OLD_VALUES)
    )