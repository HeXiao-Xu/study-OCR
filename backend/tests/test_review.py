"""S3-9 双人复核：二审权限/幂等/闸门/锁定/列表筛选。"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from decimal import Decimal

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text as sa_text
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
def review_database() -> Iterator[URL]:
    with temporary_database("study_ocr_review_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(review_database: URL):
    eng = create_engine(review_database.render_as_string(hide_password=False), poolclass=NullPool)
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


def _complete_fields() -> dict[str, str]:
    return {
        "invoice_no": "12345678",
        "invoice_date": "2026-03-01",
        "amount": "100.00",
        "tax_amount": "13.00",
        "total_amount": "113.00",
    }


def _confirmed_invoice(client, session_factory, owner: User) -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=owner.id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin", page_count=1)
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=owner.id, file_id=file.id)
        for name, value in _complete_fields().items():
            db.add(InvoiceField(invoice_id=invoice.id, field_name=name, field_value=value,
                                normalized_value=value, raw_text=value, source="ocr",
                                confidence=Decimal("0.990"), confirmed=True))
        db.commit()
        invoice_id = invoice.id

    confirmed = client.post(f"{BASE}/{invoice_id}/confirm", json={}, headers=_auth(owner))
    assert confirmed.status_code == 200, confirmed.text
    return invoice_id


def test_reviewer_reviews_confirmed_invoice(client, session_factory, actors) -> None:
    invoice_id = _confirmed_invoice(client, session_factory, actors["alice"])

    response = client.post(f"{BASE}/{invoice_id}/review", json={},
                           headers=_auth(actors["rita"]))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["reviewed_by"] == actors["rita"].id
    assert data["reviewed_at"] is not None
    assert data["status"] == "confirmed" and data["extract_status"] == "confirmed"
    assert data["version"] == 2          # confirm +1，review +1


def test_review_permissions(client, session_factory, actors) -> None:
    invoice_id = _confirmed_invoice(client, session_factory, actors["alice"])

    assert client.post(f"{BASE}/{invoice_id}/review", json={},
                       headers=_auth(actors["alice"])).status_code == 403
    assert client.post(f"{BASE}/{invoice_id}/review", json={},
                       headers=_auth(actors["bob"])).status_code == 404
    assert client.post(f"{BASE}/{invoice_id}/review", json={},
                       headers=_auth(actors["ada"])).status_code == 404


def test_review_requires_confirmed(client, session_factory, actors) -> None:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=actors["alice"].id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin", page_count=1)
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=actors["alice"].id, file_id=file.id)
        db.commit()
        invoice_id = invoice.id

    response = client.post(f"{BASE}/{invoice_id}/review", json={},
                           headers=_auth(actors["rita"]))
    assert response.status_code == 409


def test_review_is_idempotent(client, session_factory, actors) -> None:
    invoice_id = _confirmed_invoice(client, session_factory, actors["alice"])

    first = client.post(f"{BASE}/{invoice_id}/review", json={}, headers=_auth(actors["rita"]))
    second = client.post(f"{BASE}/{invoice_id}/review", json={}, headers=_auth(actors["rita"]))

    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["version"] == second.json()["data"]["version"] == 2


def test_reviewer_can_patch_before_review(client, session_factory, actors) -> None:
    invoice_id = _confirmed_invoice(client, session_factory, actors["alice"])
    version = client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["rita"])) \
        .json()["data"]["invoice"]["version"]

    patched = client.patch(
        f"{BASE}/{invoice_id}/fields",
        json={"version": version,
              "fields": [{"field_name": "buyer_name", "value": "复核前修正", "confirm": True}]},
        headers=_auth(actors["rita"]),
    )
    assert patched.status_code == 200, patched.text


def test_review_gate_rejects_inconsistent_amount(client, session_factory, actors) -> None:
    invoice_id = _confirmed_invoice(client, session_factory, actors["alice"])
    # 直接改库模拟"确认后被改坏"的状态：PATCH 自己会拦金额不自洽，构造不出该状态
    with session_factory() as db:
        db.execute(sa_text(
            "update invoice_fields set field_value='999.00', normalized_value='999.00' "
            "where invoice_id=:i and field_name='total_amount'"), {"i": invoice_id})
        db.execute(sa_text("update invoices set total_amount=999.00 where id=:i"),
                   {"i": invoice_id})
        db.commit()

    blocked = client.post(f"{BASE}/{invoice_id}/review", json={},
                          headers=_auth(actors["rita"]))
    assert blocked.status_code == 422
    assert blocked.json()["code"] == 42201
    assert any(item["field_name"] == "total_amount"
               for item in blocked.json()["data"]["fields"])


def test_review_locks_field_edits(client, session_factory, actors) -> None:
    invoice_id = _confirmed_invoice(client, session_factory, actors["alice"])
    assert client.post(f"{BASE}/{invoice_id}/review", json={},
                       headers=_auth(actors["rita"])).status_code == 200

    version = client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["rita"])) \
        .json()["data"]["invoice"]["version"]
    locked = client.patch(
        f"{BASE}/{invoice_id}/fields",
        json={"version": version,
              "fields": [{"field_name": "buyer_name", "value": "复核后改", "confirm": True}]},
        headers=_auth(actors["rita"]),
    )
    assert locked.status_code == 409
    assert locked.json()["code"] == 40900


def test_review_status_filter(client, session_factory, actors) -> None:
    reviewed_id = _confirmed_invoice(client, session_factory, actors["alice"])
    pending_id = _confirmed_invoice(client, session_factory, actors["alice"])
    assert client.post(f"{BASE}/{reviewed_id}/review", json={},
                       headers=_auth(actors["rita"])).status_code == 200

    pending = client.get(f"{BASE}?review_status=pending",
                         headers=_auth(actors["rita"])).json()["data"]["items"]
    reviewed = client.get(f"{BASE}?review_status=reviewed",
                          headers=_auth(actors["rita"])).json()["data"]["items"]

    assert [item["id"] for item in pending] == [pending_id]
    assert [item["id"] for item in reviewed] == [reviewed_id]