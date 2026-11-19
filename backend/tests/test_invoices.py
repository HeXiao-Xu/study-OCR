"""票据 CRUD / 状态机 / 字段修改的集成测试（临时 PostgreSQL 库）。

跑法：`cd backend && ../.venv/Scripts/python.exe -m pytest -m integration tests/test_invoices.py -q`
建票没有 HTTP 入口（契约里建票走 S2-6 的 upload），测试用内部 `create_draft()`；
HTTP 链路按 `create_draft → PATCH fields → GET detail` 走一遍（curl 等价）。
"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.exceptions import ConflictError
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.services import invoice_service
from tests.db_helpers import alembic_config, temporary_database

pytestmark = pytest.mark.integration

BASE = "/api/v1/invoices"


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def invoice_database() -> Iterator[URL]:
    with temporary_database("study_ocr_inv_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(invoice_database: URL):
    eng = create_engine(
        invoice_database.render_as_string(hide_password=False), poolclass=NullPool
    )
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def clean_db(engine):
    with engine.begin() as conn:
        conn.execute(sa_text("TRUNCATE users RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def actors(session_factory, clean_db) -> dict[str, User]:
    with session_factory() as db:
        users = {
            "alice": User(username="alice", real_name="A", role="submitter", status="active"),
            "bob": User(username="bob", real_name="B", role="submitter", status="active"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


@pytest.fixture()
def client(session_factory, clean_db) -> Iterator[TestClient]:
    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 数据助手
# --------------------------------------------------------------------------- #
def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(
        user_id=user.id,
        role=user.role,
        token_type=TokenType.ACCESS,
        ttl_seconds=60,
    )
    return {"Authorization": f"Bearer {token}"}


def _make_invoice(session_factory, uploader_id: int, **fields) -> int:
    with session_factory() as db:
        file = InvoiceFile(
            uploader_id=uploader_id,
            filename="a.jpg",
            mime="image/jpeg",
            size=1024,
            sha256=secrets.token_hex(32),
            storage_key=f"invoices/{secrets.token_hex(8)}.bin",
        )
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(
            db, uploader_id=uploader_id, file_id=file.id, **fields
        )
        return invoice.id


def _set_status(session_factory, invoice_id: int, status: str) -> None:
    with session_factory() as db:
        db.execute(
            sa_text("update invoices set status = :status where id = :id"),
            {"status": status, "id": invoice_id},
        )
        db.commit()


def _seed_field(session_factory, invoice_id: int, name: str, value: str) -> None:
    with session_factory() as db:
        db.add(
            InvoiceField(
                invoice_id=invoice_id,
                field_name=name,
                field_value=value,
                normalized_value=value,
                source="ocr",
                confidence=Decimal("0.900"),
            )
        )
        db.commit()


def _seed_job(session_factory, invoice_id: int) -> int:
    with session_factory() as db:
        job = OcrJob(invoice_id=invoice_id, stage="detect", status="succeeded", elapsed_ms=120)
        db.add(job)
        db.commit()
        return job.id


def _get_invoice(session_factory, invoice_id: int) -> Invoice:
    with session_factory() as db:
        return db.get(Invoice, invoice_id)


def _patch(client, invoice_id, user, **body):
    return client.patch(f"{BASE}/{invoice_id}/fields", json=body, headers=_auth(user))


# --------------------------------------------------------------------------- #
# 服务层：建草稿 / 状态机
# --------------------------------------------------------------------------- #
def test_create_draft_binds_owner_and_file(session_factory, actors) -> None:
    invoice_id = _make_invoice(
        session_factory,
        actors["alice"].id,
        invoice_code="1234567890",
        invoice_no="12345678",
        total_amount=Decimal("100.00"),
    )
    invoice = _get_invoice(session_factory, invoice_id)
    assert invoice.uploader_id == actors["alice"].id
    assert invoice.status == "draft"
    assert invoice.version == 0
    assert invoice.ocr_status == "pending"


def test_transition_allows_contract_path(session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        invoice_service.transition(db, invoice, "confirmed", actors["alice"])
        assert invoice.status == "confirmed" and invoice.version == 1
        invoice_service.transition(db, invoice, "in_report", actors["alice"])
        invoice_service.transition(db, invoice, "archived", actors["alice"])
        assert invoice.version == 3

        with pytest.raises(ConflictError):
            invoice_service.transition(db, invoice, "void", actors["alice"])


@pytest.mark.parametrize("target", ["archived", "in_report"])
def test_transition_rejects_illegal_jump(session_factory, actors, target: str) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        with pytest.raises(ConflictError):
            invoice_service.transition(db, invoice, target, actors["alice"])


def test_transition_rejects_stale_version(session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        with pytest.raises(ConflictError):
            invoice_service.transition(db, invoice, "confirmed", actors["alice"], expected_version=7)


# --------------------------------------------------------------------------- #
# 列表
# --------------------------------------------------------------------------- #
def test_list_visibility_and_default_void_exclusion(client, session_factory, actors) -> None:
    alice_draft = _make_invoice(session_factory, actors["alice"].id, total_amount=Decimal("1.00"))
    alice_confirmed = _make_invoice(session_factory, actors["alice"].id, total_amount=Decimal("2.00"))
    alice_void = _make_invoice(session_factory, actors["alice"].id, total_amount=Decimal("3.00"))
    bob_draft = _make_invoice(session_factory, actors["bob"].id, total_amount=Decimal("4.00"))
    _set_status(session_factory, alice_confirmed, "confirmed")
    _set_status(session_factory, alice_void, "void")

    alice = client.get(BASE, headers=_auth(actors["alice"]))
    assert alice.status_code == 200
    page = alice.json()["data"]
    assert set(page) == {"items", "total", "page", "page_size"}
    assert page["total"] == 2
    assert {item["id"] for item in page["items"]} == {alice_draft, alice_confirmed}

    reviewer = client.get(BASE, headers=_auth(actors["rita"]))
    assert reviewer.json()["data"]["total"] == 3
    assert {item["id"] for item in reviewer.json()["data"]["items"]} == {
        alice_draft,
        alice_confirmed,
        bob_draft,
    }

    admin = client.get(BASE, headers=_auth(actors["ada"]))
    assert admin.status_code == 404
    assert admin.json()["code"] == 40400

    void_page = client.get(f"{BASE}?status=void", headers=_auth(actors["alice"]))
    assert {item["id"] for item in void_page.json()["data"]["items"]} == {alice_void}


def test_list_filters(client, session_factory, actors) -> None:
    target = _make_invoice(
        session_factory,
        actors["alice"].id,
        invoice_no="88888888",
        seller_name="目标销售有限公司",
        invoice_date=date(2026, 5, 1),
        invoice_type="vat_general",
        total_amount=Decimal("500.00"),
    )
    other = _make_invoice(
        session_factory,
        actors["alice"].id,
        invoice_no="99999999",
        seller_name="别的公司",
        invoice_date=date(2025, 1, 1),
        invoice_type="unknown",
        total_amount=Decimal("10.00"),
    )
    confirmed = _make_invoice(session_factory, actors["alice"].id)
    _set_status(session_factory, confirmed, "confirmed")
    digital = _make_invoice(
        session_factory,
        actors["alice"].id,
        invoice_no="77777777",
        invoice_type="vat_digital",
    )

    cases = [
        ("q=8888", {target}),
        ("q=目标销售", {target}),
        ("date_from=2026-01-01", {target}),
        ("date_to=2025-12-31", {other}),
        ("invoice_type=vat_general", {target}),
        ("invoice_type=vat_digital", {digital}),
        ("extract_status=raw", {target, other, confirmed, digital}),
        ("status=draft", {target, other, digital}),
    ]
    for query, expected in cases:
        response = client.get(f"{BASE}?{query}", headers=_auth(actors["alice"]))
        assert response.status_code == 200, query
        ids = {item["id"] for item in response.json()["data"]["items"]}
        assert ids == expected, query


def test_list_pagination_and_sort(client, session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, total_amount=Decimal("1.00"))
    second = _make_invoice(session_factory, actors["alice"].id, total_amount=Decimal("2.00"))

    page1 = client.get(f"{BASE}?page=1&page_size=1&sort_by=total_amount&order=asc", headers=_auth(actors["alice"]))
    page2 = client.get(f"{BASE}?page=2&page_size=1&sort_by=total_amount&order=asc", headers=_auth(actors["alice"]))
    assert page1.json()["data"]["total"] == 2
    assert [item["id"] for item in page1.json()["data"]["items"]] == [first]
    assert [item["id"] for item in page2.json()["data"]["items"]] == [second]


@pytest.mark.parametrize(
    "query",
    ["status=bogus", "extract_status=bogus", "date_from=2026-13-40", "sort_by=drop%20table"],
)
def test_list_rejects_invalid_query(client, actors, query: str) -> None:
    response = client.get(f"{BASE}?{query}", headers=_auth(actors["alice"]))
    assert response.status_code == 422
    assert response.json()["code"] == 42200


def test_list_never_exposes_storage_key(client, session_factory, actors) -> None:
    _make_invoice(session_factory, actors["alice"].id)
    response = client.get(BASE, headers=_auth(actors["alice"]))
    assert "storage_key" not in response.json()["data"]["items"][0]


# --------------------------------------------------------------------------- #
# 详情
# --------------------------------------------------------------------------- #
def test_detail_returns_fields_latest_job_and_money_strings(
    client, session_factory, actors
) -> None:
    invoice_id = _make_invoice(
        session_factory, actors["alice"].id, total_amount=Decimal("1234.56")
    )
    _seed_field(session_factory, invoice_id, "buyer_name", "测试公司")
    job_id = _seed_job(session_factory, invoice_id)

    response = client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["alice"]))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["invoice"]["id"] == invoice_id
    assert data["invoice"]["total_amount"] == "1234.56"  # 两位小数字符串，不是浮点
    assert "storage_key" not in data["invoice"]
    assert [item["field_name"] for item in data["fields"]] == ["buyer_name"]
    assert data["latest_job"]["job_id"] == job_id
    assert data["latest_job"]["stage"] == "detect"


def test_detail_isolated_and_admin_404(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)

    assert client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["bob"])).status_code == 404
    assert client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["ada"])).status_code == 404
    assert client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["rita"])).status_code == 200
    assert client.get(f"{BASE}/999999", headers=_auth(actors["alice"])).status_code == 404


# --------------------------------------------------------------------------- #
# 软删
# --------------------------------------------------------------------------- #
def test_delete_is_soft_and_idempotent(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    headers = _auth(actors["alice"])

    first = client.delete(f"{BASE}/{invoice_id}", headers=headers)
    assert first.status_code == 204
    assert first.content == b""

    detail = client.get(f"{BASE}/{invoice_id}", headers=headers)
    assert detail.json()["data"]["invoice"]["status"] == "void"

    again = client.delete(f"{BASE}/{invoice_id}", headers=headers)
    assert again.status_code == 204


@pytest.mark.parametrize("status", ["in_report", "archived"])
def test_delete_rejects_non_deletable_states(client, session_factory, actors, status: str) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _set_status(session_factory, invoice_id, status)
    response = client.delete(f"{BASE}/{invoice_id}", headers=_auth(actors["alice"]))
    assert response.status_code == 409
    assert response.json()["code"] == 40900


def test_delete_other_submitter_and_reviewer_404(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    for actor in (actors["bob"], actors["rita"], actors["ada"]):
        response = client.delete(f"{BASE}/{invoice_id}", headers=_auth(actor))
        assert response.status_code == 404


def test_void_releases_invoice_number(session_factory, actors) -> None:
    with session_factory() as db:
        first = invoice_service.create_draft(
            db,
            uploader_id=actors["alice"].id,
            file_id=_file_id(session_factory, actors["alice"].id),
            invoice_code="1234567890",
            invoice_no="12345678",
        )
        invoice_service.soft_delete(db, actors["alice"], first.id)

    with session_factory() as db:
        # void 释放了部分唯一索引，同号可以再建
        second = invoice_service.create_draft(
            db,
            uploader_id=actors["alice"].id,
            file_id=_file_id(session_factory, actors["alice"].id),
            invoice_code="1234567890",
            invoice_no="12345678",
        )
        assert second.id != first.id


def _file_id(session_factory, uploader_id: int) -> int:
    with session_factory() as db:
        file = InvoiceFile(
            uploader_id=uploader_id,
            filename="b.jpg",
            mime="image/jpeg",
            size=2048,
            sha256=secrets.token_hex(32),
            storage_key=f"invoices/{secrets.token_hex(8)}.bin",
        )
        db.add(file)
        db.commit()
        return file.id


# --------------------------------------------------------------------------- #
# 字段修改
# --------------------------------------------------------------------------- #
def test_submitter_updates_non_amount_field(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    response = _patch(
        client,
        invoice_id,
        actors["alice"],
        version=0,
        fields=[{"field_name": "buyer_name", "value": "新购买方", "confirm": True}],
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["version"] == 1
    assert data["updated"] == [
        {"field_name": "buyer_name", "before": None, "after": "新购买方"}
    ]
    assert data["needs_confirm"] == []
    assert data["conflicts"] == []

    detail = client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["alice"]))
    invoice = detail.json()["data"]["invoice"]
    assert invoice["buyer_name"] == "新购买方"
    assert invoice["version"] == 1
    field = detail.json()["data"]["fields"][0]
    assert (field["source"], field["confirmed"], field["need_review"]) == ("manual", True, False)


def test_submitter_cannot_touch_amount_fields(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    response = _patch(
        client,
        invoice_id,
        actors["alice"],
        version=0,
        fields=[{"field_name": "total_amount", "value": "100.00"}],
        confirm_amount=True,
    )
    assert response.status_code == 403
    assert response.json()["code"] == 40301


def test_reviewer_amount_update_requires_confirm_flag(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    body = {
        "version": 0,
        "fields": [{"field_name": "total_amount", "value": "113.00"}],
    }
    missing = _patch(client, invoice_id, actors["rita"], **body)
    assert missing.status_code == 422
    assert missing.json()["code"] == 42202

    ok_response = _patch(
        client,
        invoice_id,
        actors["rita"],
        version=0,
        fields=[
            {"field_name": "amount", "value": "100.00"},
            {"field_name": "tax_amount", "value": "13.00"},
            {"field_name": "total_amount", "value": "113.00"},
        ],
        confirm_amount=True,
    )
    assert ok_response.status_code == 200, ok_response.text
    assert ok_response.json()["data"]["version"] == 1


def test_fields_version_and_state_conflicts(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    stale = _patch(
        client,
        invoice_id,
        actors["alice"],
        version=9,
        fields=[{"field_name": "buyer_name", "value": "X"}],
    )
    assert stale.status_code == 409

    _set_status(session_factory, invoice_id, "confirmed")
    not_draft = _patch(
        client,
        invoice_id,
        actors["alice"],
        version=0,
        fields=[{"field_name": "buyer_name", "value": "X"}],
    )
    assert not_draft.status_code == 409


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("not_a_field", "x"), ("invoice_date", "2026-13-40"), ("invoice_no", "123")],
)
def test_fields_reject_invalid_values(
    client, session_factory, actors, field_name: str, value: str
) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id, invoice_type="vat_general")
    response = _patch(
        client,
        invoice_id,
        actors["alice"],
        version=0,
        fields=[{"field_name": field_name, "value": value}],
    )
    assert response.status_code == 422
    assert response.json()["code"] == 42200


def test_unresolved_conflict_rolls_back(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(
        session_factory,
        actors["alice"].id,
        amount=Decimal("100.00"),
        tax_amount=Decimal("13.00"),
        total_amount=Decimal("113.00"),
    )
    response = _patch(
        client,
        invoice_id,
        actors["rita"],
        version=0,
        fields=[{"field_name": "total_amount", "value": "999.00"}],
        confirm_amount=True,
    )
    assert response.status_code == 422
    assert response.json()["code"] == 42201

    detail = client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["alice"]))
    assert detail.json()["data"]["invoice"]["version"] == 0
    assert detail.json()["data"]["invoice"]["total_amount"] == "113.00"


def test_confirm_flag_controls_needs_confirm(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)

    confirmed = _patch(
        client,
        invoice_id,
        actors["alice"],
        version=0,
        fields=[{"field_name": "buyer_name", "value": "A", "confirm": True}],
    )
    assert confirmed.json()["data"]["needs_confirm"] == []

    pending = _patch(
        client,
        invoice_id,
        actors["alice"],
        version=1,
        fields=[{"field_name": "check_code", "value": "123456", "confirm": False}],
    )
    assert pending.json()["data"]["needs_confirm"] == ["check_code"]


def test_admin_cannot_patch_fields(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    response = _patch(
        client,
        invoice_id,
        actors["ada"],
        version=0,
        fields=[{"field_name": "buyer_name", "value": "X"}],
    )
    assert response.status_code == 404


def test_create_patch_detail_chain(client, session_factory, actors) -> None:
    """curl 等价链路：建单（内部 factory）→ 改字段 → 查详情。"""
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    patched = _patch(
        client,
        invoice_id,
        actors["alice"],
        version=0,
        fields=[
            {"field_name": "buyer_name", "value": "链路购买方", "confirm": True},
            {"field_name": "check_code", "value": "123456", "confirm": True},
        ],
    )
    assert patched.status_code == 200
    detail = client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["alice"]))
    invoice = detail.json()["data"]["invoice"]
    assert invoice["buyer_name"] == "链路购买方"
    assert invoice["version"] == 1
    assert {item["field_name"] for item in detail.json()["data"]["fields"]} == {
        "buyer_name",
        "check_code",
    }