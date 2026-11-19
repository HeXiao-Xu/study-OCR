"""上传接口集成测试（临时 PostgreSQL 库 + 假 MinIO；默认跳过，`-m integration` 跑）。

覆盖：魔数校验/真图校验/PDF 页数/大小上限/权限/sha256 去重（去重前不写对象）/
落库失败补偿/文件名清洗/响应不泄露对象名。
"""
from __future__ import annotations

import hashlib
import io
import re
import secrets
from collections.abc import Iterator
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from alembic import command
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import create_engine, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import ServiceUnavailableError
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.services import upload_service
from app.services.storage import build_object_key, get_storage
from tests.db_helpers import alembic_config, temporary_database

UPLOAD = "/api/v1/invoices/upload"


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def upload_database() -> Iterator[URL]:
    with temporary_database("study_ocr_up_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(upload_database: URL):
    eng = create_engine(
        upload_database.render_as_string(hide_password=False), poolclass=NullPool
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
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


class FakeStorage:
    """与 StorageService 同协议；记录写入对象与补偿删除。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []
        self.deleted: list[str] = []

    def build_object_key(self) -> str:
        return f"invoices/2026/11/{secrets.token_hex(16)}.bin"

    def put(self, key: str, fileobj, length: int, content_type: str) -> None:
        data = fileobj.read()
        assert len(data) == length
        self.objects[key] = data
        self.put_calls.append(key)

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects


def _make_settings(**overrides) -> Settings:
    base = {
        "environment": "test",
        "database_url": "postgresql://studyocr:test@127.0.0.1:5433/studyocr",
        "redis_url": "redis://:test@127.0.0.1:6380/0",
        "minio_endpoint": "127.0.0.1:9002",
        "minio_access_key": "test",
        "minio_secret_key": "test",
        "minio_bucket": "study-ocr-test",
        "jwt_secret": "test_secret_" + "x" * 32,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def upload_env(session_factory, clean_db):
    storage = FakeStorage()
    settings = _make_settings()
    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_storage] = lambda: storage

    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client,
            storage=storage,
            settings=settings,
            app=app,
            session_factory=session_factory,
        )


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


def _jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def _tiff_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="TIFF")
    return buffer.getvalue()


def _pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _upload(env, user, content: bytes, filename: str = "a.jpg", content_type: str = "image/jpeg"):
    return env.client.post(
        UPLOAD,
        files={"file": (filename, content, content_type)},
        headers=_auth(user),
    )


def _counts(env) -> tuple[int, int, int]:
    with env.session_factory() as db:
        return (
            len(db.scalars(select(InvoiceFile)).all()),
            len(db.scalars(select(Invoice)).all()),
            len(db.scalars(select(OcrJob)).all()),
        )


# --------------------------------------------------------------------------- #
# 成功与去重
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_upload_creates_file_invoice_and_pending_job(upload_env, actors) -> None:
    content = _jpeg_bytes()
    response = _upload(upload_env, actors["alice"], content)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["code"] == 0
    data = body["data"]
    assert set(data) == {
        "invoice_id",
        "file_id",
        "job_id",
        "sha256",
        "status",
        "ocr_status",
        "dedup",
    }
    assert data["dedup"] == {"hit": False, "invoice_id": None}
    assert data["sha256"] == hashlib.sha256(content).hexdigest()
    assert data["status"] == "draft"
    assert data["ocr_status"] == "pending"
    assert "storage_key" not in response.text

    with upload_env.session_factory() as db:
        file_row = db.get(InvoiceFile, data["file_id"])
        assert file_row is not None
        assert (file_row.sha256, file_row.size, file_row.mime) == (
            data["sha256"],
            len(content),
            "image/jpeg",
        )
        assert file_row.is_cipher is False
        assert file_row.page_count == 1
        assert file_row.storage_key.startswith("invoices/")

        invoice = db.get(Invoice, data["invoice_id"])
        assert invoice.file_id == file_row.id and invoice.uploader_id == actors["alice"].id
        job = db.get(OcrJob, data["job_id"])
        assert (job.invoice_id, job.stage, job.status) == (invoice.id, "preprocess", "pending")

    assert len(upload_env.storage.objects) == 1
    assert next(iter(upload_env.storage.objects)) == file_row.storage_key


@pytest.mark.integration
def test_duplicate_upload_returns_409_without_second_object(upload_env, actors) -> None:
    content = _jpeg_bytes()
    first = _upload(upload_env, actors["alice"], content)
    assert first.status_code == 202

    second = _upload(upload_env, actors["alice"], content)
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == 40901
    assert body["data"]["invoice_id"] == first.json()["data"]["invoice_id"]

    assert len(upload_env.storage.put_calls) == 1
    assert len(upload_env.storage.objects) == 1
    assert _counts(upload_env) == (1, 1, 1)


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_upload_rejects_empty_file(upload_env, actors) -> None:
    response = _upload(upload_env, actors["alice"], b"")
    assert response.status_code == 422
    assert response.json()["code"] == 42200
    assert _counts(upload_env) == (0, 0, 0)


@pytest.mark.integration
def test_upload_rejects_oversized_file(upload_env, actors) -> None:
    upload_env.app.dependency_overrides[get_settings] = lambda: _make_settings(max_upload_mb=1)
    content = b"\xff\xd8\xff" + b"0" * (1024 * 1024)
    response = _upload(upload_env, actors["alice"], content)
    assert response.status_code == 413
    assert response.json()["code"] == 41300
    assert _counts(upload_env) == (0, 0, 0)


@pytest.mark.integration
def test_upload_rejects_fake_image(upload_env, actors) -> None:
    response = _upload(upload_env, actors["alice"], b"this is not an image", "a.jpg")
    assert response.status_code == 422
    assert response.json()["code"] == 42200


@pytest.mark.integration
def test_upload_rejects_tiff(upload_env, actors) -> None:
    response = _upload(upload_env, actors["alice"], _tiff_bytes(), "a.tiff", "image/tiff")
    assert response.status_code == 422
    assert response.json()["code"] == 42200


@pytest.mark.integration
def test_upload_pdf_page_limit(upload_env, actors) -> None:
    too_many = _upload(upload_env, actors["alice"], _pdf_bytes(6), "a.pdf", "application/pdf")
    assert too_many.status_code == 422

    ok_response = _upload(upload_env, actors["alice"], _pdf_bytes(1), "b.pdf", "application/pdf")
    assert ok_response.status_code == 202
    with upload_env.session_factory() as db:
        file_row = db.get(InvoiceFile, ok_response.json()["data"]["file_id"])
        assert (file_row.mime, file_row.page_count) == ("application/pdf", 1)


# --------------------------------------------------------------------------- #
# 权限与失败补偿
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_upload_permissions(upload_env, actors) -> None:
    content = _jpeg_bytes()
    assert _upload(upload_env, actors["rita"], content).status_code == 403
    assert _upload(upload_env, actors["ada"], content).status_code == 404
    assert upload_env.client.post(UPLOAD, files={"file": ("a.jpg", content, "image/jpeg")}).status_code == 401


@pytest.mark.integration
def test_persist_failure_deletes_stored_object(upload_env, actors, monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("模拟落库失败")

    monkeypatch.setattr(upload_service, "_persist", _boom)
    response = _upload(upload_env, actors["alice"], _jpeg_bytes())

    assert response.status_code == 500
    assert response.json()["code"] == 50000
    assert len(upload_env.storage.put_calls) == 1
    assert upload_env.storage.deleted == upload_env.storage.put_calls
    assert upload_env.storage.objects == {}
    assert _counts(upload_env) == (0, 0, 0)


@pytest.mark.integration
def test_storage_failure_returns_503(upload_env, actors, monkeypatch) -> None:
    def _fail(self, key, fileobj, length, content_type):
        raise ServiceUnavailableError("对象存储不可用")

    monkeypatch.setattr(FakeStorage, "put", _fail)
    response = _upload(upload_env, actors["alice"], _jpeg_bytes())
    assert response.status_code == 503
    assert response.json()["code"] == 50300
    assert _counts(upload_env) == (0, 0, 0)


@pytest.mark.integration
def test_filename_is_sanitized(upload_env, actors) -> None:
    response = _upload(upload_env, actors["alice"], _jpeg_bytes(), "../../etc/passwd")
    assert response.status_code == 202
    with upload_env.session_factory() as db:
        file_row = db.get(InvoiceFile, response.json()["data"]["file_id"])
        assert file_row.filename == "passwd"
        assert "passwd" not in file_row.storage_key


# --------------------------------------------------------------------------- #
# 纯函数（默认 pytest 跑）
# --------------------------------------------------------------------------- #
def test_build_object_key_format() -> None:
    key = build_object_key(datetime(2026, 11, 19, tzinfo=timezone.utc))
    assert re.fullmatch(r"invoices/2026/11/[0-9a-f]{32}\.bin", key)