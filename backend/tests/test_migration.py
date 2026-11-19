"""迁移集成测试（需要本地 Docker PostgreSQL，默认不跑）。

跑法：`cd backend && ../.venv/Scripts/python.exe -m pytest -m integration -q`

原则：
- **不污染开发库**：每次建临时库 `study_ocr_mig_<hex>`，用完删掉；
- 用 `command.upgrade/downgrade/check` 走真实 Alembic 路径（不手写 DDL）；
- 约束用例都在事务里跑，结束回滚，库保持干净。
"""
from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

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
from tests.db_helpers import alembic_config, list_tables, temporary_database

pytestmark = pytest.mark.integration

BUSINESS_TABLES = {
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


# --------------------------------------------------------------------------- #
# 临时库由 tests/db_helpers.py 提供（与 test_auth 共用同一套建库/删库逻辑）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def migrated_database() -> Iterator[URL]:
    with temporary_database("study_ocr_mig_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(migrated_database: URL):
    eng = create_engine(
        migrated_database.render_as_string(hide_password=False), poolclass=NullPool
    )
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def conn(engine):
    connection = engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


# --------------------------------------------------------------------------- #
# 插入助手（都不写 id：主键是 GENERATED ALWAYS）
# --------------------------------------------------------------------------- #
def _insert(conn, table: str, **values):
    columns = ", ".join(values)
    binds = ", ".join(f":{key}" for key in values)
    return conn.execute(
        sa_text(f"insert into {table} ({columns}) values ({binds}) returning id"), values
    ).scalar_one()


def _user(conn, username: str = "alice", *, role: str = "submitter") -> int:
    return _insert(conn, "users", username=username, role=role)


def _certificate(conn, *, fingerprint: str | None = None, serial: str = "01") -> int:
    now = datetime.now(timezone.utc)
    return _insert(
        conn,
        "certificates",
        owner_type="server",
        owner_id=None,
        subject_dn="CN=财务服务器,O=测试大学",
        serial_no=serial,
        issuer_dn="CN=测试CA",
        public_key_pem="-----BEGIN PUBLIC KEY-----",
        cert_pem="-----BEGIN CERTIFICATE-----",
        not_before=now,
        not_after=now + timedelta(days=365),
        fingerprint_sha256=fingerprint or secrets.token_hex(32),
    )


def _file(conn, uploader_id: int, *, sha256: str | None = None) -> int:
    return _insert(
        conn,
        "invoice_files",
        uploader_id=uploader_id,
        filename="a.jpg",
        mime="image/jpeg",
        size=1024,
        sha256=sha256 or secrets.token_hex(32),
        storage_key=f"invoices/2026/11/{secrets.token_hex(8)}.bin",
    )


def _invoice(conn, uploader_id: int, file_id: int, **overrides) -> int:
    values = {"uploader_id": uploader_id, "file_id": file_id}
    values.update(overrides)
    return _insert(conn, "invoices", **values)


def _form(conn, applicant_id: int) -> int:
    return _insert(
        conn,
        "report_forms",
        report_no=f"RPT-202611-{secrets.token_hex(3)}",
        applicant_id=applicant_id,
        title="测试报销单",
    )


def _signature(conn, form_id: int, signer_id: int, cert_id: int, **overrides) -> int:
    values = {
        "form_id": form_id,
        "signer_id": signer_id,
        "algo": "SM2",
        "digest_algo": "SM3",
        "digest_hex": "b" * 64,
        "signature_b64": "c2ln",
        "cert_id": cert_id,
    }
    values.update(overrides)
    return _insert(conn, "signatures", **values)


def _envelope(conn, cert_id: int, **overrides) -> int:
    values = {
        "key_algo": "SM2",
        "cipher_algo": "SM4-CBC-HMAC-SM3",
        "wrapped_key_b64": "d3JhcHBlZA==",
        "iv_b64": "aXY=",
        "mac_b64": "bWFj",
        "ciphertext_b64": "Y2lwaGVy",
        "payload_sha256": "c" * 64,
        "recipient_cert_id": cert_id,
    }
    values.update(overrides)
    return _insert(conn, "envelopes", **values)


def _expect_integrity_error(conn, action) -> None:
    """约束必须真的拦住：失败后回滚到 SAVEPOINT，外层事务仍可用。"""
    with pytest.raises(IntegrityError):
        with conn.begin_nested():
            action()


# --------------------------------------------------------------------------- #
# 结构与往返
# --------------------------------------------------------------------------- #
def test_upgrade_creates_16_business_tables(migrated_database: URL) -> None:
    assert list_tables(migrated_database) == BUSINESS_TABLES | {"alembic_version"}


def test_primary_keys_are_identity_columns(migrated_database: URL, engine) -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            sa_text(
                "select table_name, identity_generation from information_schema.columns"
                " where table_schema = 'public' and column_name = 'id'"
            )
        ).fetchall()
    assert {row[0] for row in rows} == BUSINESS_TABLES
    assert all(row[1] == "ALWAYS" for row in rows)


def test_downgrade_then_upgrade_roundtrip() -> None:
    with temporary_database() as url:
        config = alembic_config(url)
        command.upgrade(config, "head")
        assert list_tables(url) == BUSINESS_TABLES | {"alembic_version"}

        command.downgrade(config, "base")
        assert list_tables(url) == {"alembic_version"}

        command.upgrade(config, "head")
        assert list_tables(url) == BUSINESS_TABLES | {"alembic_version"}


def test_alembic_check_reports_no_drift(migrated_database: URL) -> None:
    # 有差异会抛 AutogenerateDiffsDetected，等于测试失败
    command.check(alembic_config(migrated_database))


def test_enum_checks_in_database_match_code(migrated_database: URL, engine) -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            sa_text(
                "select t.relname, c.conname, pg_get_constraintdef(c.oid)"
                " from pg_constraint c"
                " join pg_class t on t.oid = c.conrelid"
                " join pg_namespace n on n.oid = t.relnamespace"
                " where c.contype = 'c' and n.nspname = 'public'"
            )
        ).fetchall()
    definitions = {(row[0], row[1]): row[2] for row in rows}

    for table_name, column, enum_cls in ENUM_COLUMNS:
        name = f"ck_{table_name}_{column}"
        definition = definitions.get((table_name, name))
        assert definition, f"库中缺少 {name}"
        # PG 会把 IN (...) 重写成 = ANY (ARRAY['a'::text, ...])，按引号抽取值
        values = tuple(re.findall(r"'([^']*)'", definition))
        assert values == enum_values(enum_cls), f"{name} 与 enums.py 不一致"


# --------------------------------------------------------------------------- #
# 业务不变式：库必须真的拦得住
# --------------------------------------------------------------------------- #
def test_duplicate_invoice_number_rejected_unless_void(conn) -> None:
    user = _user(conn)
    first = _invoice(
        conn, user, _file(conn, user), invoice_code="1234567890", invoice_no="12345678"
    )

    _expect_integrity_error(
        conn,
        lambda: _invoice(
            conn, user, _file(conn, user), invoice_code="1234567890", invoice_no="12345678"
        ),
    )

    # 作废后释放唯一约束，允许重新录入同一张票
    conn.execute(sa_text("update invoices set status = 'void' where id = :id"), {"id": first})
    _invoice(conn, user, _file(conn, user), invoice_code="1234567890", invoice_no="12345678")


def test_duplicate_file_sha256_rejected(conn) -> None:
    user = _user(conn)
    digest = secrets.token_hex(32)
    _file(conn, user, sha256=digest)
    _expect_integrity_error(conn, lambda: _file(conn, user, sha256=digest))


def test_only_one_active_signature_per_form(conn) -> None:
    user = _user(conn)
    cert = _certificate(conn)
    form = _form(conn, user)
    first = _signature(conn, form, user, cert, version=1)

    _expect_integrity_error(
        conn, lambda: _signature(conn, form, user, cert, version=2)
    )

    conn.execute(
        sa_text("update signatures set status = 'void' where id = :id"), {"id": first}
    )
    _signature(conn, form, user, cert, version=2)


def test_only_one_active_template_per_type(conn) -> None:
    first = _insert(
        conn,
        "ticket_templates",
        type_code="vat_general",
        type_name="增值税电子普通发票",
        field_specs_json='{"total_amount": {"strategy": "inline_right"}}',
        version=1,
        active=True,
    )

    _expect_integrity_error(
        conn,
        lambda: _insert(
            conn,
            "ticket_templates",
            type_code="vat_general",
            type_name="增值税电子普通发票 v2",
            field_specs_json="{}",
            version=2,
            active=True,
        ),
    )

    conn.execute(
        sa_text("update ticket_templates set active = false where id = :id"), {"id": first}
    )
    _insert(
        conn,
        "ticket_templates",
        type_code="vat_general",
        type_name="增值税电子普通发票 v2",
        field_specs_json="{}",
        version=2,
        active=True,
    )


def test_one_envelope_per_file_and_form(conn) -> None:
    user = _user(conn)
    cert = _certificate(conn)
    file_id = _file(conn, user)
    form = _form(conn, user)

    _envelope(conn, cert, file_id=file_id)
    _expect_integrity_error(conn, lambda: _envelope(conn, cert, file_id=file_id))

    _envelope(conn, cert, form_id=form)
    _expect_integrity_error(conn, lambda: _envelope(conn, cert, form_id=form))


def test_duplicate_field_line_and_item_rejected(conn) -> None:
    user = _user(conn)
    invoice = _invoice(conn, user, _file(conn, user))
    form = _form(conn, user)

    _insert(conn, "invoice_fields", invoice_id=invoice, field_name="total_amount")
    _expect_integrity_error(
        conn,
        lambda: _insert(
            conn, "invoice_fields", invoice_id=invoice, field_name="total_amount"
        ),
    )

    _insert(conn, "ocr_lines", invoice_id=invoice, page_no=1, line_no=0, text="价税合计")
    _expect_integrity_error(
        conn,
        lambda: _insert(
            conn, "ocr_lines", invoice_id=invoice, page_no=1, line_no=0, text="重复行"
        ),
    )

    _insert(conn, "report_items", form_id=form, invoice_id=invoice, amount=Decimal("10.00"))
    _expect_integrity_error(
        conn,
        lambda: _insert(
            conn, "report_items", form_id=form, invoice_id=invoice, amount=Decimal("1.00")
        ),
    )


def test_verified_consistency_check(conn) -> None:
    user = _user(conn)
    cert = _certificate(conn)
    form = _form(conn, user)

    # 未验签：verify_result 为空、verified=false 合法
    first = _signature(conn, form, user, cert, version=1)
    conn.execute(
        sa_text("update signatures set status = 'void' where id = :id"), {"id": first}
    )
    # 已验签成功
    second = _signature(conn, form, user, cert, version=2, verified=True, verify_result="ok")
    conn.execute(
        sa_text("update signatures set status = 'void' where id = :id"), {"id": second}
    )

    # 显示"已验签"但结果是失败 → 必须拦
    _expect_integrity_error(
        conn,
        lambda: _signature(
            conn,
            form,
            user,
            cert,
            version=3,
            verified=True,
            verify_result="digest_mismatch",
        ),
    )
    # 显示"未验签"但结果是 ok → 同假不同真，也拦
    _expect_integrity_error(
        conn,
        lambda: _signature(conn, form, user, cert, version=4, verified=False, verify_result="ok"),
    )


def test_invalid_enum_rejected(conn) -> None:
    _expect_integrity_error(conn, lambda: _user(conn, "bob", role="superuser"))


def test_envelope_required_fields(conn) -> None:
    user = _user(conn)
    cert = _certificate(conn)

    _expect_integrity_error(conn, lambda: _envelope(conn, cert, wrapped_key_b64=""))
    _expect_integrity_error(
        conn,
        lambda: _envelope(conn, cert, ciphertext_b64=None, ciphertext_storage_key=None),
    )


def test_negative_amounts_rejected(conn) -> None:
    user = _user(conn)
    file_id = _file(conn, user)
    _expect_integrity_error(
        conn, lambda: _invoice(conn, user, file_id, total_amount=Decimal("-0.01"))
    )

    invoice = _invoice(conn, user, file_id)
    form = _form(conn, user)
    _expect_integrity_error(
        conn,
        lambda: _insert(
            conn, "report_items", form_id=form, invoice_id=invoice, amount=Decimal("-1.00")
        ),
    )


def test_canonical_json_roundtrip_is_byte_exact(conn) -> None:
    user = _user(conn)
    form = _form(conn, user)
    payload = '{"b":  1, "a":2, "金额":"100.00"}'
    conn.execute(
        sa_text("update report_forms set canonical_json = :payload where id = :id"),
        {"payload": payload, "id": form},
    )
    stored = conn.execute(
        sa_text("select canonical_json from report_forms where id = :id"), {"id": form}
    ).scalar_one()
    assert stored == payload


def test_decimal_roundtrip(conn) -> None:
    user = _user(conn)
    invoice = _invoice(conn, user, _file(conn, user), amount=Decimal("1234.56"), total_amount=Decimal("1300.00"))
    row = conn.execute(
        sa_text("select amount, total_amount from invoices where id = :id"), {"id": invoice}
    ).one()
    assert row[0] == Decimal("1234.56")
    assert row[1] == Decimal("1300.00")


def test_identity_assigns_ids(conn) -> None:
    first = _user(conn, "user_one")
    second = _user(conn, "user_two")
    assert isinstance(first, int) and isinstance(second, int)
    assert second > first


def test_invalid_foreign_key_rejected(conn) -> None:
    _expect_integrity_error(conn, lambda: _file(conn, 987654321))


def test_certificate_owner_pair_check(conn) -> None:
    now = datetime.now(timezone.utc)
    base = {
        "subject_dn": "CN=用户证书",
        "serial_no": "02",
        "issuer_dn": "CN=测试CA",
        "public_key_pem": "pem",
        "cert_pem": "pem",
        "not_before": now,
        "not_after": now + timedelta(days=30),
        "fingerprint_sha256": secrets.token_hex(32),
    }

    _expect_integrity_error(
        conn, lambda: _insert(conn, "certificates", owner_type="user", owner_id=None, **base)
    )
    _expect_integrity_error(
        conn, lambda: _insert(conn, "certificates", owner_type="server", owner_id=1, **base)
    )


def test_key_record_owner_check(conn) -> None:
    cert = _certificate(conn)
    common = {"cert_id": cert, "storage_path": f"secrets/keys/{secrets.token_hex(4)}.p8.enc", "algo": "SM2"}

    # 签名密钥必须有归属人
    _expect_integrity_error(
        conn, lambda: _insert(conn, "key_records", key_type="sign", user_id=None, **common)
    )
    # 财务服务器信封密钥没有归属人
    _insert(
        conn,
        "key_records",
        key_type="envelope",
        user_id=None,
        **{**common, "storage_path": f"secrets/keys/server_{secrets.token_hex(4)}.p8.enc"},
    )


def test_hex_digest_format_check(conn) -> None:
    user = _user(conn)
    _expect_integrity_error(conn, lambda: _file(conn, user, sha256="A" * 64))
    _expect_integrity_error(conn, lambda: _file(conn, user, sha256="z" * 64))


def test_quality_flag_check(conn) -> None:
    user = _user(conn)
    _expect_integrity_error(
        conn, lambda: _invoice(conn, user, _file(conn, user), quality_flag="fail")
    )