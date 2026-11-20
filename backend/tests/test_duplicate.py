"""S3-7 重复检测：强（duplicate_of/同票号/同影像）、弱（同金额+日期+销方）、权限、confirm 阻断、并发索引。"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from decimal import Decimal

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text as sa_text, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.users import User
from app.services import duplicate, invoice_service
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/invoices"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def dup_database() -> Iterator[URL]:
    with temporary_database("study_ocr_dup_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(dup_database: URL):
    eng = create_engine(dup_database.render_as_string(hide_password=False), poolclass=NullPool)
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


def _client(session_factory, clean_db) -> Iterator[TestClient]:
    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(user_id=user.id, role=user.role,
                            token_type=TokenType.ACCESS, ttl_seconds=60)
    return {"Authorization": f"Bearer {token}"}


def _make_invoice(session_factory, uploader_id: int, *, invoice_no: str | None = None,
                  invoice_code: str | None = None, duplicate_of: int | None = None,
                  sha256: str | None = None) -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=sha256 or secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin", page_count=1)
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=uploader_id, file_id=file.id)
        invoice.invoice_no = invoice_no
        invoice.invoice_code = invoice_code
        invoice.duplicate_of = duplicate_of
        db.commit()
        return invoice.id


def _fields(session_factory, invoice_id: int, values: dict[str, str]) -> None:
    with session_factory() as db:
        for name, value in values.items():
            db.add(InvoiceField(invoice_id=invoice_id, field_name=name, field_value=value,
                                normalized_value=value, raw_text=value, source="ocr",
                                confidence=Decimal("0.990")))
        db.commit()


def test_duplicate_of_is_strong(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    second = _make_invoice(session_factory, actors["alice"].id, duplicate_of=first)

    with session_factory() as db:
        report = duplicate.check_duplicates(db, db.get(Invoice, second))

    assert report.level == "strong"
    assert report.matches[0].kind == "invoice_no"
    assert report.matches[0].invoice_id == first


def test_code_no_branch_mapping_is_strong(monkeypatch, session_factory, actors) -> None:
    """同票号查询是纵深防御：唯一索引让正常路径不可能存在两条同号非 void 票，单测映射逻辑。"""
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    second = _make_invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        other = db.get(Invoice, first)
        monkeypatch.setattr(duplicate, "_code_no_matches", lambda db_, invoice_: [other])
        report = duplicate.check_duplicates(db, db.get(Invoice, second))

    assert report.level == "strong"
    assert report.matches[0].kind == "invoice_no"
    assert {item.invoice_id for item in report.matches} == {first}


def test_weak_duplicate_three_dimensions(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    _fields(session_factory, first, {"total_amount": "100.00",
                                     "invoice_date": "2026-03-01",
                                     "seller_tax_no": "91420100MA4K000001"})
    second = _make_invoice(session_factory, actors["alice"].id, invoice_no="22222222")
    _fields(session_factory, second, {"total_amount": "100.00",
                                      "invoice_date": "2026-03-01",
                                      "seller_tax_no": "91420100MA4K000001"})

    with session_factory() as db:
        report = duplicate.check_duplicates(db, db.get(Invoice, second))

    assert report.level == "weak"
    assert report.matches[0].kind == "weak"
    assert report.matches[0].invoice_no == "11111111"


def test_weak_requires_all_three_and_excludes_void(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    _fields(session_factory, first, {"total_amount": "100.00",
                                     "invoice_date": "2026-03-01",
                                     "seller_tax_no": "91420100MA4K000001"})
    second = _make_invoice(session_factory, actors["alice"].id, invoice_no="22222222")
    _fields(session_factory, second, {"total_amount": "100.00",
                                      "invoice_date": "2026-03-01"})   # 缺销方税号

    with session_factory() as db:
        assert duplicate.check_duplicates(db, db.get(Invoice, second)).level == "none"

    with session_factory() as db:
        db.execute(sa_text("update invoices set status='void' where id=:id"), {"id": first})
        db.commit()
        assert duplicate.check_duplicates(db, db.get(Invoice, second)).level == "none"


def test_duplicate_check_endpoint_and_permissions(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    second = _make_invoice(session_factory, actors["alice"].id, duplicate_of=first)

    with _client(session_factory, None) as client:
        owner = client.get(f"{BASE}/{second}/duplicate-check", headers=_auth(actors["alice"]))
        reviewer = client.get(f"{BASE}/{second}/duplicate-check", headers=_auth(actors["rita"]))
        other = client.get(f"{BASE}/{second}/duplicate-check", headers=_auth(actors["bob"]))
        admin = client.get(f"{BASE}/{second}/duplicate-check", headers=_auth(actors["ada"]))

    assert owner.status_code == 200
    data = owner.json()["data"]
    assert data["level"] == "strong"
    assert data["matches"][0]["invoice_id"] == first
    assert "buyer_name" not in data["matches"][0]
    assert reviewer.status_code == 200
    assert other.status_code == 404 and admin.status_code == 404


def test_confirm_blocks_strong_duplicate(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    second = _make_invoice(session_factory, actors["alice"].id, duplicate_of=first)
    _fields(session_factory, second, {"invoice_no": "22222222",
                                      "invoice_date": "2026-03-01",
                                      "amount": "100.00", "tax_amount": "13.00",
                                      "total_amount": "113.00"})
    _fields(session_factory, first, {"invoice_no": "11111111",
                                     "invoice_date": "2026-03-01",
                                     "amount": "100.00", "tax_amount": "13.00",
                                     "total_amount": "113.00"})

    with _client(session_factory, None) as client:
        blocked = client.post(f"{BASE}/{second}/confirm", json={},
                              headers=_auth(actors["alice"]))
        plain = client.post(f"{BASE}/{first}/confirm", json={},
                            headers=_auth(actors["alice"]))

    assert blocked.status_code == 409
    assert blocked.json()["code"] == 40900
    assert blocked.json()["data"]["invoice_id"] == first
    assert plain.status_code == 200          # 无重复的票照常确认（回归）


def test_unique_index_blocks_concurrent_same_number(session_factory, actors) -> None:
    """并发最后一道闸：两个 session 同时写同 (code,no)，第二个必须 IntegrityError。"""
    first = _make_invoice(session_factory, actors["alice"].id)
    second = _make_invoice(session_factory, actors["alice"].id)

    with session_factory() as db1, session_factory() as db2:
        db1.execute(update(Invoice).where(Invoice.id == first)
                    .values(invoice_code="1234567890", invoice_no="11111111"))
        db1.commit()
        with pytest.raises(IntegrityError):
            db2.execute(update(Invoice).where(Invoice.id == second)
                        .values(invoice_code="1234567890", invoice_no="11111111"))
            db2.commit()


def test_image_match_mapping_is_strong(monkeypatch, session_factory, actors) -> None:
    """影像重复受 uq_invoice_files_sha256 限制造不出两条同 sha，单测映射逻辑。"""
    first = _make_invoice(session_factory, actors["alice"].id)
    second = _make_invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        other = db.get(Invoice, first)
        monkeypatch.setattr(duplicate, "_image_matches", lambda db_, invoice_: [other])
        report = duplicate.check_duplicates(db, db.get(Invoice, second))

    assert report.level == "strong"
    assert report.matches[0].kind == "image"