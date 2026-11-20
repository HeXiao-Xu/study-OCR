"""S3-6 整单确认：闸门（必填/冲突/低置信/校验）、权限、幂等、版本冲突、并发 409。"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from decimal import Decimal

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.users import User
from app.services import invoice_service
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/invoices"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def confirm_database() -> Iterator[URL]:
    with temporary_database("study_ocr_confirm_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(confirm_database: URL):
    eng = create_engine(confirm_database.render_as_string(hide_password=False), poolclass=NullPool)
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


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(user_id=user.id, role=user.role,
                            token_type=TokenType.ACCESS, ttl_seconds=60)
    return {"Authorization": f"Bearer {token}"}


def _make_invoice(session_factory, uploader_id: int, *, invoice_type: str = "unknown") -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin", page_count=1)
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(
            db, uploader_id=uploader_id, file_id=file.id, invoice_type=invoice_type
        )
        db.commit()
        return invoice.id


def _seed_fields(session_factory, invoice_id: int, fields: dict[str, str], *,
                 need_review: bool = False, confirmed: bool = False,
                 conflict: bool = False) -> None:
    with session_factory() as db:
        for name, value in fields.items():
            db.add(InvoiceField(invoice_id=invoice_id, field_name=name,
                                field_value=value, normalized_value=value,
                                raw_text=value, source="ocr", confidence=Decimal("0.990"),
                                need_review=need_review, confirmed=confirmed,
                                conflict=conflict,
                                conflict_value="999.99" if conflict else None))
        db.commit()


def _complete_fields() -> dict[str, str]:
    return {
        "invoice_no": "12345678",
        "invoice_date": "2026-03-01",
        "amount": "100.00",
        "tax_amount": "13.00",
        "total_amount": "113.00",
    }


def _confirm(client, invoice_id, user, **body):
    return client.post(f"{BASE}/{invoice_id}/confirm", json=body, headers=_auth(user))


def test_confirm_happy_path(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())

    response = _confirm(client, invoice_id, actors["alice"], version=0)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "confirmed" and data["extract_status"] == "confirmed"
    assert data["version"] == 1
    with session_factory() as db:
        assert db.get(Invoice, invoice_id).status == "confirmed"


def test_confirm_is_idempotent(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())

    assert _confirm(client, invoice_id, actors["alice"]).status_code == 200
    second = _confirm(client, invoice_id, actors["alice"])

    assert second.status_code == 200
    assert second.json()["data"]["version"] == 1      # 不再 +1


def test_confirm_missing_required_lists_field(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    fields = _complete_fields()
    fields.pop("invoice_no")
    _seed_fields(session_factory, invoice_id, fields)

    response = _confirm(client, invoice_id, actors["alice"])

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == 42201
    assert any(item["field_name"] == "invoice_no" for item in payload["data"]["fields"])


def test_confirm_unresolved_conflict_gate(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields(), conflict=True)

    response = _confirm(client, invoice_id, actors["alice"])

    assert response.status_code == 422
    assert any(item["field_name"] == "total_amount"
               for item in response.json()["data"]["fields"])


def test_confirm_marks_all_fields_confirmed(client, session_factory, actors) -> None:
    """整单确认即"已核对全部字段"：低置信字段一并落 confirmed，不再逐字段点确认（D-070）。"""
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())
    _seed_fields(session_factory, invoice_id, {"buyer_name": "武汉某某有限公司"},
                 need_review=True)

    response = _confirm(client, invoice_id, actors["alice"])

    assert response.status_code == 200, response.text
    with session_factory() as db:
        rows = db.scalars(select(InvoiceField).where(
            InvoiceField.invoice_id == invoice_id)).all()
        assert all(row.confirmed for row in rows)
        assert not any(row.need_review for row in rows)


def test_confirm_validator_failure(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    fields = _complete_fields()
    fields["total_amount"] = "999.00"                 # 不等于 100 + 13
    _seed_fields(session_factory, invoice_id, fields)

    response = _confirm(client, invoice_id, actors["alice"])

    assert response.status_code == 422
    assert any(item["field_name"] == "total_amount"
               for item in response.json()["data"]["fields"])


def test_confirm_permissions(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())

    assert _confirm(client, invoice_id, actors["bob"]).status_code == 404
    assert _confirm(client, invoice_id, actors["rita"]).status_code == 403
    assert _confirm(client, invoice_id, actors["ada"]).status_code == 404


def test_confirm_rejects_non_draft_and_stale_version(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())
    with session_factory() as db:
        db.execute(sa_text("update invoices set status='void' where id=:id"), {"id": invoice_id})
        db.commit()

    assert _confirm(client, invoice_id, actors["alice"]).status_code == 409

    invoice_id2 = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id2, _complete_fields())

    assert _confirm(client, invoice_id2, actors["alice"], version=99).status_code == 409


def test_concurrent_patch_same_version_one_conflicts(client, session_factory, actors) -> None:
    """任务卡验收：两个窗口同时改同一字段，第二次必须 409。"""
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())
    body = {"version": 0,
            "fields": [{"field_name": "buyer_name", "value": "甲", "confirm": True}]}

    first = client.patch(f"{BASE}/{invoice_id}/fields", json=body,
                         headers=_auth(actors["alice"]))
    second = client.patch(f"{BASE}/{invoice_id}/fields", json=body,
                          headers=_auth(actors["alice"]))

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == 40900