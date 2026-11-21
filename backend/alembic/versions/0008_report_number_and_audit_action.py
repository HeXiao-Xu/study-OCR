"""report_no sequence + audit action report_create

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-21

S5-1（D-084）两件事，都是"契约要求但库里还没有"：

1. 单号 `RPT-YYYYMM-序号` 需要并发安全的计数器 → PostgreSQL 序列 `report_no_seq`。
   为什么不是 `count()+1`：并发必重号；为什么不是"按月序列"：运行期建序列或第 17 张计数器表，
   契约都没给，全局序列已满足"不重号"。
2. `POST /reports` 要写 `audit_logs.action='report_create'`（API.md §4 备注），
   但 SCHEMA §9.19b 与 `enums.AuditAction` 都没有该值 → 放开 CHECK。

与 0002/0005 同样手写而不是 autogenerate：Alembic 不比对既有表上的 CHECK，且序列不在 autogenerate 视野内。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
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
    "field_reread",
)
_NEW_VALUES = (*_OLD_VALUES, "report_create")


def _check_sql(values: Sequence[str]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"action IN ({allowed})"


def upgrade() -> None:
    # 非事务性对象：回滚不退号，单号只求唯一、不求连续
    op.execute("CREATE SEQUENCE IF NOT EXISTS report_no_seq START WITH 1 INCREMENT BY 1")

    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action", "audit_logs", _check_sql(_NEW_VALUES)
    )


def downgrade() -> None:
    # 审计日志追加写、不可删：已有 report_create 行时回退会让这些行语义失效
    bind = op.get_bind()
    used = bind.execute(
        sa.text("select count(*) from audit_logs where action = 'report_create'")
    ).scalar_one()
    if used:
        raise RuntimeError(
            "审计表中已有 report_create 的行，禁止 downgrade（审计不可删）"
        )

    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action", "audit_logs", _check_sql(_OLD_VALUES)
    )

    op.execute("DROP SEQUENCE IF EXISTS report_no_seq")