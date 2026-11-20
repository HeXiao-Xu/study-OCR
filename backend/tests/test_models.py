"""模型元数据单测：不需要数据库。

这些用例守的是"模型 = SCHEMA"，不是"代码能跑"：
- 16 张表、表名、契约外列零出现；
- 枚举 CHECK 取值与 `enums.py` **完全相等**（不是包含）；
- 5 条部分唯一索引的谓词逐字正确；
- 类型/可空/默认值/外键/索引在模型侧就是对的。

迁移与库的对照在 `tests/test_migration.py`（integration）。
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest
from sqlalchemy import BigInteger, Date, DateTime, Numeric, Text, UniqueConstraint

from app.models import Base
from app.models.enums import (
    AuditAction,
    AuditActorRole,
    AuditTargetType,
    CertOwnerType,
    CipherAlgo,
    DigestAlgo,
    EnvelopeKeyAlgo,
    ExtractStatus,
    FieldSource,
    FieldValidator,
    InvoiceStatus,
    KeyAlgo,
    KeyType,
    OcrStage,
    OcrStatus,
    QualityFlag,
    ReportStatus,
    ReviewDecision,
    SignatureAlgo,
    SignatureStatus,
    TicketType,
    UserRole,
    UserStatus,
    VerifyResult,
    enum_values,
)

EXPECTED_TABLES = {
    "users",
    "certificates",
    "key_records",
    "invoice_files",
    "invoices",
    "invoice_fields",
    "ocr_lines",
    "ocr_jobs",
    "ticket_templates",
    "field_rules",
    "report_forms",
    "report_items",
    "signatures",
    "envelopes",
    "review_logs",
    "audit_logs",
}

ENUM_COLUMNS: list[tuple[str, str, type]] = [
    ("users", "role", UserRole),
    ("users", "status", UserStatus),
    ("certificates", "owner_type", CertOwnerType),
    ("certificates", "key_algo", KeyAlgo),
    ("key_records", "key_type", KeyType),
    ("key_records", "algo", KeyAlgo),
    ("invoices", "invoice_type", TicketType),
    ("invoices", "ocr_status", OcrStatus),
    ("invoices", "extract_status", ExtractStatus),
    ("invoices", "status", InvoiceStatus),
    ("invoices", "quality_flag", QualityFlag),
    ("ticket_templates", "type_code", TicketType),
    ("field_rules", "validator", FieldValidator),
    ("invoice_fields", "source", FieldSource),
    ("ocr_jobs", "stage", OcrStage),
    ("ocr_jobs", "status", OcrStatus),
    ("report_forms", "status", ReportStatus),
    ("report_forms", "digest_algo", DigestAlgo),
    ("signatures", "algo", SignatureAlgo),
    ("signatures", "digest_algo", DigestAlgo),
    ("signatures", "status", SignatureStatus),
    ("signatures", "verify_result", VerifyResult),
    ("envelopes", "key_algo", EnvelopeKeyAlgo),
    ("envelopes", "cipher_algo", CipherAlgo),
    ("review_logs", "decision", ReviewDecision),
    ("review_logs", "verify_result", VerifyResult),
    ("audit_logs", "actor_role", AuditActorRole),
    ("audit_logs", "action", AuditAction),
    ("audit_logs", "target_type", AuditTargetType),
]

PARTIAL_UNIQUE_INDEXES: list[tuple[str, str, list[str], str]] = [
    ("uq_invoices_code_no", "invoices", ["invoice_code", "invoice_no"], "status <> 'void'"),
    ("uq_signatures_form_active", "signatures", ["form_id"], "status = 'active'"),
    ("uq_ticket_templates_active_type", "ticket_templates", ["type_code"], "active"),
    ("uq_envelopes_file", "envelopes", ["file_id"], "file_id IS NOT NULL"),
    ("uq_envelopes_form", "envelopes", ["form_id"], "form_id IS NOT NULL"),
]

UNIQUE_CONSTRAINTS: list[tuple[str, str]] = [
    ("users", "uq_users_username"),
    ("certificates", "uq_certificates_fingerprint"),
    ("certificates", "uq_certificates_issuer_serial"),
    ("key_records", "uq_key_records_storage_path"),
    ("invoice_files", "uq_invoice_files_sha256"),
    ("invoice_files", "uq_invoice_files_storage_key"),
    ("invoices", "uq_invoices_file_id"),
    ("ticket_templates", "uq_ticket_templates_type_version"),
    ("invoice_fields", "uq_invoice_fields_invoice_field"),
    ("ocr_lines", "uq_ocr_lines_invoice_page_line"),
    ("report_forms", "uq_report_forms_report_no"),
    ("report_forms", "uq_report_forms_idempotency_key"),
    ("report_items", "uq_report_items_form_invoice"),
    ("signatures", "uq_signatures_form_version"),
    ("audit_logs", "uq_audit_logs_entry_hash"),
]

SERVER_DEFAULTS: list[tuple[str, str, str]] = [
    ("users", "role", "'submitter'"),
    ("users", "status", "'active'"),
    ("certificates", "key_algo", "'SM2'"),
    ("certificates", "revoked", "false"),
    ("invoice_files", "is_cipher", "true"),
    ("invoice_files", "page_count", "1"),
    ("ticket_templates", "version", "1"),
    ("ticket_templates", "active", "false"),
    ("field_rules", "priority", "100"),
    ("field_rules", "active", "true"),
    ("invoices", "invoice_type", "'unknown'"),
    ("invoices", "ocr_status", "'pending'"),
    ("invoices", "extract_status", "'raw'"),
    ("invoices", "status", "'draft'"),
    ("invoices", "version", "0"),
    ("invoice_fields", "source", "'ocr'"),
    ("invoice_fields", "source_page_no", "1"),
    ("invoice_fields", "need_review", "false"),
    ("invoice_fields", "conflict", "false"),
    ("invoice_fields", "confirmed", "false"),
    ("ocr_jobs", "status", "'pending'"),
    ("ocr_jobs", "engine", "'paddleocr'"),
    ("ocr_jobs", "retry_count", "0"),
    ("report_forms", "total_amount", "0"),
    ("report_forms", "item_count", "0"),
    ("report_forms", "status", "'draft'"),
    ("report_forms", "digest_algo", "'SM3'"),
    ("report_forms", "version", "0"),
    ("signatures", "version", "1"),
    ("signatures", "status", "'active'"),
    ("signatures", "verified", "false"),
    ("review_logs", "decrypt_ok", "false"),
]


def _table(name: str):
    return Base.metadata.tables[name]


def _constraint(table_name: str, constraint_name: str):
    for constraint in _table(table_name).constraints:
        if constraint.name == constraint_name:
            return constraint
    raise AssertionError(f"缺少约束 {constraint_name}（表 {table_name}）")


def _index(table_name: str, index_name: str):
    for index in _table(table_name).indexes:
        if index.name == index_name:
            return index
    raise AssertionError(f"缺少索引 {index_name}（表 {table_name}）")


def test_metadata_has_exactly_16_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert len(Base.metadata.tables) == 16


def test_primary_keys_are_always_identity() -> None:
    for name in EXPECTED_TABLES:
        table = _table(name)
        assert len(table.primary_key.columns) == 1, f"{name} 主键必须单列"
        column = next(iter(table.primary_key.columns))
        assert isinstance(column.type, BigInteger), f"{name}.id 必须是 BIGINT"
        assert column.identity is not None, f"{name}.id 必须是 IDENTITY"
        assert column.identity.always is True, f"{name}.id 必须是 GENERATED ALWAYS"


def test_no_contract_external_columns() -> None:
    forbidden = {"created_by", "tenant_id", "deleted_at", "is_deleted"}
    for name in EXPECTED_TABLES:
        columns = {column.name for column in _table(name).columns}
        assert not (columns & forbidden), f"{name} 出现契约外列：{columns & forbidden}"


def _quoted_values(sql: str) -> tuple[str, ...]:
    match = re.search(r"IN \((.+)\)", sql, flags=re.IGNORECASE | re.DOTALL)
    assert match, f"CHECK 不是 IN 形式：{sql}"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


@pytest.mark.parametrize(("table_name", "column", "enum_cls"), ENUM_COLUMNS)
def test_enum_check_values_equal_constants(table_name, column, enum_cls) -> None:
    constraint = _constraint(table_name, f"ck_{table_name}_{column}")
    assert _quoted_values(str(constraint.sqltext)) == enum_values(enum_cls)


@pytest.mark.parametrize(("index_name", "table_name", "columns", "predicate"), PARTIAL_UNIQUE_INDEXES)
def test_partial_unique_indexes(index_name, table_name, columns, predicate) -> None:
    index = _index(table_name, index_name)
    assert index.unique is True
    assert [column.name for column in index.columns] == columns
    where = index.dialect_options["postgresql"]["where"]
    assert str(where) == predicate


@pytest.mark.parametrize(("table_name", "constraint_name"), UNIQUE_CONSTRAINTS)
def test_unique_constraints(table_name, constraint_name) -> None:
    constraint = _constraint(table_name, constraint_name)
    assert isinstance(constraint, UniqueConstraint)


@pytest.mark.parametrize(("table_name", "column", "expected"), SERVER_DEFAULTS)
def test_server_defaults(table_name, column, expected) -> None:
    default = _table(table_name).c[column].server_default
    assert default is not None, f"{table_name}.{column} 缺 server_default"
    assert str(default.arg) == expected


def test_timestamptz_everywhere() -> None:
    for name in EXPECTED_TABLES:
        for column in _table(name).columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True, f"{name}.{column.name} 必须带时区"


def test_canonical_json_is_text_not_jsonb() -> None:
    column = _table("report_forms").c["canonical_json"]
    assert isinstance(column.type, Text)
    assert column.type.__class__.__name__ == "Text"


def test_invoice_date_is_business_date() -> None:
    column = _table("invoices").c["invoice_date"]
    assert isinstance(column.type, Date)
    assert not isinstance(column.type, DateTime)


def test_money_and_confidence_precision() -> None:
    for column in ("amount", "tax_amount", "total_amount"):
        type_ = _table("invoices").c[column].type
        assert isinstance(type_, Numeric)
        assert (type_.precision, type_.scale) == (12, 2)
    confidence = _table("invoice_fields").c["confidence"].type
    assert (confidence.precision, confidence.scale) == (4, 3)
    score = _table("ocr_lines").c["score"].type
    assert (score.precision, score.scale) == (4, 3)


def test_gin_index_on_field_specs() -> None:
    index = _index("ticket_templates", "ix_templates_specs")
    assert index.dialect_options["postgresql"]["using"] == "gin"


def test_desc_expression_indexes() -> None:
    for table_name, index_name in (
        ("invoices", "ix_invoices_uploader_created"),
        ("report_forms", "ix_report_forms_applicant_created"),
        ("audit_logs", "ix_audit_logs_created"),
    ):
        index = _index(table_name, index_name)
        assert any(str(expr) == "created_at DESC" for expr in index.expressions), (
            f"{index_name} 缺少 created_at DESC 表达式"
        )


def test_foreign_keys_targets_and_no_cascade() -> None:
    expected = {
        ("invoice_files", "uploader_id"): ("users", "id"),
        ("invoices", "uploader_id"): ("users", "id"),
        ("invoices", "file_id"): ("invoice_files", "id"),
        ("invoices", "template_id"): ("ticket_templates", "id"),
        ("invoices", "duplicate_of"): ("invoices", "id"),
        ("invoice_fields", "rule_id"): ("field_rules", "id"),
        ("ocr_lines", "job_id"): ("ocr_jobs", "id"),
        ("report_items", "form_id"): ("report_forms", "id"),
        ("signatures", "cert_id"): ("certificates", "id"),
        ("envelopes", "recipient_cert_id"): ("certificates", "id"),
        ("audit_logs", "actor_id"): ("users", "id"),
    }
    for (table_name, column_name), (target_table, target_column) in expected.items():
        column = _table(table_name).c[column_name]
        foreign_key = next(iter(column.foreign_keys))
        assert (foreign_key.column.table.name, foreign_key.column.name) == (
            target_table,
            target_column,
        )
        # 本课题不做级联删除：票据与签名"删不掉、只能作废"
        assert foreign_key.ondelete is None


def test_polymorphic_owner_has_no_foreign_key() -> None:
    assert _table("certificates").c["owner_id"].foreign_keys == set()
    assert _table("audit_logs").c["target_id"].foreign_keys == set()


def test_hex_check_text() -> None:
    for table_name, column in (
        ("invoice_files", "sha256"),
        ("certificates", "fingerprint_sha256"),
        ("signatures", "digest_hex"),
        ("report_forms", "digest_hex"),
        ("envelopes", "payload_sha256"),
        ("audit_logs", "prev_hash"),
        ("audit_logs", "entry_hash"),
    ):
        constraint = _constraint(table_name, f"ck_{table_name}_{column}")
        assert "~ '^[0-9a-f]{64}$'" in str(constraint.sqltext)


def test_consistency_checks_present() -> None:
    checks = {
        ("signatures", "ck_signatures_verified_consistency"): "verified = (verify_result = 'ok')",
        ("certificates", "ck_certificates_owner_pair"): "owner_type = 'user' AND owner_id IS NOT NULL",
        ("key_records", "ck_key_records_owner"): "user_id IS NOT NULL OR key_type = 'envelope'",
        ("envelopes", "ck_envelopes_ciphertext"): (
            "ciphertext_b64 IS NOT NULL OR ciphertext_storage_key IS NOT NULL"
        ),
        ("envelopes", "ck_envelopes_wrapped_key"): "wrapped_key_b64 <> ''",
        ("key_records", "ck_key_records_storage_path"): "PRIVATE KEY",
    }
    for (table_name, constraint_name), fragment in checks.items():
        assert fragment in str(_constraint(table_name, constraint_name).sqltext)


def test_updated_at_has_python_onupdate() -> None:
    for table_name in ("users", "ticket_templates", "field_rules", "invoices", "report_forms"):
        assert _table(table_name).c["updated_at"].onupdate is not None


def test_decimal_is_used_for_money() -> None:
    assert Decimal("1234.56") == Decimal("1234.56")
    # 金额列的类型必须是 Numeric（SQLAlchemy 默认 asdecimal=True）；
    # 若有人改成 Float，这里会先炸
    assert _table("report_items").c["amount"].type.asdecimal is True
    assert _table("report_forms").c["total_amount"].type.asdecimal is True

def test_audit_action_check_includes_field_reread() -> None:
    """S3-3：reocr 需要 field_reread；枚举与 CHECK 必须同源（0005 只放开 CHECK）。"""
    from app.models.enums import AuditAction, enum_values

    assert "field_reread" in enum_values(AuditAction)
