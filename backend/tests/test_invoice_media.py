"""S3-5 HTTP 集成：`GET /invoices/{id}/image`（原图/PDF/预处理、权限、明文闸门、ETag）。"""
from __future__ import annotations

import hashlib
import io
import secrets
from collections.abc import Iterator

import pytest
from alembic import command
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.users import User
from app.services import invoice_service
from app.services.storage import get_storage
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/invoices"
pytestmark = pytest.mark.integration


def _jpeg(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _pdf(pages: int = 2) -> bytes:
    import pymupdf

    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 100), f"page {index + 1}")
    return document.tobytes()


class FakeStorage:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        return self.payload


@pytest.fixture(scope="module")
def media_database() -> Iterator[URL]:
    with temporary_database("study_ocr_media_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(media_database: URL):
    eng = create_engine(media_database.render_as_string(hide_password=False), poolclass=NullPool)
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


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(user_id=user.id, role=user.role,
                            token_type=TokenType.ACCESS, ttl_seconds=60)
    return {"Authorization": f"Bearer {token}"}


def _make_invoice(session_factory, uploader_id: int, *, mime: str = "image/jpeg",
                  page_count: int = 1, is_cipher: bool = False) -> int:
    with session_factory() as db:
        file = InvoiceFile(
            uploader_id=uploader_id, filename="a.bin", mime=mime, size=1024,
            sha256=secrets.token_hex(32), storage_key=f"invoices/{secrets.token_hex(8)}.bin",
            is_cipher=is_cipher, page_count=page_count,
        )
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=uploader_id, file_id=file.id)
        db.commit()
        return invoice.id


def _client(session_factory, payload: bytes, *, settings=None) -> TestClient:
    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_storage] = lambda: FakeStorage(payload)
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, raise_server_exceptions=False)


def test_image_endpoint_returns_bytes_with_headers(session_factory, actors) -> None:
    payload = _jpeg()
    invoice_id = _make_invoice(session_factory, actors["alice"].id)

    with _client(session_factory, payload) as client:
        response = client.get(f"{BASE}/{invoice_id}/image", headers=_auth(actors["alice"]))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["etag"].strip('"') == hashlib.sha256(payload).hexdigest()
    assert response.content == payload


def test_image_endpoint_permissions(session_factory, actors) -> None:
    payload = _jpeg()
    invoice_id = _make_invoice(session_factory, actors["alice"].id)

    with _client(session_factory, payload) as client:
        assert client.get(f"{BASE}/{invoice_id}/image",
                          headers=_auth(actors["rita"])).status_code == 200
        assert client.get(f"{BASE}/{invoice_id}/image",
                          headers=_auth(actors["bob"])).status_code == 404
        assert client.get(f"{BASE}/{invoice_id}/image",
                          headers=_auth(actors["ada"])).status_code == 404


def test_image_endpoint_cipher_returns_409(session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id, is_cipher=True)

    with _client(session_factory, _jpeg()) as client:
        response = client.get(f"{BASE}/{invoice_id}/image", headers=_auth(actors["alice"]))

    assert response.status_code == 409
    assert response.json()["code"] == 40900


def test_image_endpoint_plaintext_disabled_returns_409(session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    disabled = get_settings().model_copy(update={"storage_allow_plaintext": False})

    with _client(session_factory, _jpeg(), settings=disabled) as client:
        response = client.get(f"{BASE}/{invoice_id}/image", headers=_auth(actors["alice"]))

    assert response.status_code == 409


def test_image_endpoint_pdf_renders_png_at_250_dpi(session_factory, actors) -> None:
    payload = _pdf(2)
    invoice_id = _make_invoice(
        session_factory, actors["alice"].id, mime="application/pdf", page_count=2
    )

    with _client(session_factory, payload) as client:
        response = client.get(f"{BASE}/{invoice_id}/image?page=2",
                              headers=_auth(actors["alice"]))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    with Image.open(io.BytesIO(response.content)) as decoded:
        assert abs(decoded.width - 2066) <= 3


def test_image_endpoint_page_out_of_range_422(session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id, page_count=1)

    with _client(session_factory, _jpeg()) as client:
        response = client.get(f"{BASE}/{invoice_id}/image?page=2",
                              headers=_auth(actors["alice"]))

    assert response.status_code == 422

# --------------------------------------------------------------------------- #
# S3-5 行框接口
# --------------------------------------------------------------------------- #
class _Prepared:
    class _Preprocess:
        import numpy as _np
        to_original = _np.array([[0.5, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 0.0, 1.0]])

    preprocess = _Preprocess()


def _add_line(session_factory, invoice_id: int, *, field_name: str | None = None) -> None:
    from decimal import Decimal

    from app.models.invoice_fields import InvoiceField
    from app.models.ocr_lines import OcrLine

    with session_factory() as db:
        db.add(OcrLine(invoice_id=invoice_id, page_no=1, line_no=0,
                       text="价税合计 ￥1,234.56", score=Decimal("0.950"),
                       box_json=[[100, 300], [900, 300], [900, 400], [100, 400]]))
        if field_name:
            db.add(InvoiceField(invoice_id=invoice_id, field_name=field_name,
                                field_value="￥1,234.56", normalized_value="1234.56",
                                raw_text="价税合计 ￥1,234.56", source="ocr",
                                confidence=Decimal("0.700"), bbox_json=[50, 150, 450, 200],
                                source_page_no=1, source_line_no=0))
        db.commit()


def test_lines_endpoint_returns_original_bbox_without_text(
    session_factory, actors, monkeypatch
) -> None:
    from app.services import line_service

    monkeypatch.setattr(line_service, "prepare_input", lambda data: _Prepared())
    payload = _jpeg()
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _add_line(session_factory, invoice_id, field_name="total_amount")

    with _client(session_factory, payload) as client:
        response = client.get(f"{BASE}/{invoice_id}/lines", headers=_auth(actors["alice"]))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["page_no"] == 1
    assert data["lines"][0]["bbox"] == [50, 150, 450, 200]     # 0.5× 映射
    assert data["lines"][0]["field_name"] == "total_amount"
    assert "text" not in data["lines"][0]


def test_lines_endpoint_permissions_and_errors(session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _add_line(session_factory, invoice_id)

    with _client(session_factory, _jpeg()) as client:
        assert client.get(f"{BASE}/{invoice_id}/lines",
                          headers=_auth(actors["rita"])).status_code == 200
        assert client.get(f"{BASE}/{invoice_id}/lines",
                          headers=_auth(actors["bob"])).status_code == 404
        assert client.get(f"{BASE}/{invoice_id}/lines",
                          headers=_auth(actors["ada"])).status_code == 404
        assert client.get(f"{BASE}/{invoice_id}/lines?page=2",
                          headers=_auth(actors["alice"])).status_code == 422


def test_lines_endpoint_cipher_returns_409(session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id, is_cipher=True)
    _add_line(session_factory, invoice_id)

    with _client(session_factory, _jpeg()) as client:
        response = client.get(f"{BASE}/{invoice_id}/lines", headers=_auth(actors["alice"]))

    assert response.status_code == 409
