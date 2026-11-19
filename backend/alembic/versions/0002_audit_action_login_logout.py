"""audit action: add login_failed / logout / forbidden

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19

S2-2 拍板（D-040）：API.md 要求登录失败、登出、越权写审计，但 SCHEMA §9.19b 原枚举没有这三个值。
本迁移只放开 CHECK，**不写任何审计行**——审计哈希链在 S4-7 实现，届时才允许 INSERT。

为什么手写而不是 autogenerate：Alembic 不比对既有表上的 CHECK 约束，
autogenerate 会认为"没有变化"，直接漏掉这次契约变更。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
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
)
_NEW_VALUES = (*_OLD_VALUES, "login_failed", "logout", "forbidden")


def _check_sql(values: Sequence[str]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"action IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action", "audit_logs", _check_sql(_NEW_VALUES)
    )


def downgrade() -> None:
    # 审计日志是追加写、不可删除；若已有使用新值的行，回退会破坏这些行的语义。
    # 这里显式拒绝，而不是删行或让 CHECK 校验在事务里以难以理解的方式失败。
    bind = op.get_bind()
    used = bind.execute(
        sa.text(
            "select count(*) from audit_logs"
            " where action in ('login_failed', 'logout', 'forbidden')"
        )
    ).scalar_one()
    if used:
        raise RuntimeError(
            "审计表中已有 login_failed/logout/forbidden 的行，禁止 downgrade（审计不可删）"
        )

    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action", "audit_logs", _check_sql(_OLD_VALUES)
    )