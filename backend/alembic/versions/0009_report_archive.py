"""report archive fields + audit action archive

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-21

S5-6（D-089）两件事：

1. `report_forms` 补归档字段：`archive_no`（UNIQUE）、`archive_at`，并加两条库级 CHECK
   （成对存在、archived 必须有归档号）；归档号由 `archive_no_seq` 生成为 `AR-YYYY-NNNN`。
2. `audit_logs.action` 放开 `archive`（归档是安全相关动作，必须进哈希链）。

与 0002/0005/0008 同样手写而不是 autogenerate：Alembic 不比对既有表上的 CHECK，
序列也不在 autogenerate 视野内。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
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
    "report_create",
)
_NEW_VALUES = (*_OLD_VALUES, "archive")


def _check_sql(values: Sequence[str]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"action IN ({allowed})"


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS archive_no_seq START WITH 1 INCREMENT BY 1")

    op.add_column("report_forms", sa.Column("archive_no", sa.Text(), nullable=True))
    op.add_column(
        "report_forms",
        sa.Column("archive_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_report_forms_archive_no", "report_forms", ["archive_no"]
    )
    op.create_check_constraint(
        "ck_report_forms_archive_pair",
        "report_forms",
        "(archive_no IS NULL) = (archive_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_report_forms_archived_fields",
        "report_forms",
        "status <> 'archived' OR archive_no IS NOT NULL",
    )
    op.create_index("ix_report_forms_archive_at", "report_forms", ["archive_at"])

    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action", "audit_logs", _check_sql(_NEW_VALUES)
    )


def downgrade() -> None:
    # 审计追加写、不可删：已有 archive 行时回退会让这些行语义失效
    bind = op.get_bind()
    used = bind.execute(
        sa.text("select count(*) from audit_logs where action = 'archive'")
    ).scalar_one()
    if used:
        raise RuntimeError("审计表中已有 archive 的行，禁止 downgrade（审计不可删）")

    existed = bind.execute(
        sa.text("select count(*) from report_forms where archive_no is not null")
    ).scalar_one()
    if existed:
        raise RuntimeError("已有归档单，禁止 downgrade（归档数据不可丢）")

    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint(
        "ck_audit_logs_action", "audit_logs", _check_sql(_OLD_VALUES)
    )

    op.drop_index("ix_report_forms_archive_at", table_name="report_forms")
    op.drop_constraint("ck_report_forms_archived_fields", "report_forms", type_="check")
    op.drop_constraint("ck_report_forms_archive_pair", "report_forms", type_="check")
    op.drop_constraint("uq_report_forms_archive_no", "report_forms", type_="unique")
    op.drop_column("report_forms", "archive_at")
    op.drop_column("report_forms", "archive_no")
    op.execute("DROP SEQUENCE IF EXISTS archive_no_seq")