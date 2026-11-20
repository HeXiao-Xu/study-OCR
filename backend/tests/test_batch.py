"""S3-8 批量上传：建单/去重/并发 3/补位/汇总/重试/30 文件验收。"""
from __future__ import annotations

import io
import secrets
from collections.abc import Iterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from PIL import Image
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
from app.services.storage import get_storage
from app.api.deps import get_task_queue
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/invoices"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def batch_database() -> Iterator[URL]:
    with temporary_database("study_ocr_batch_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(batch_database: URL):
    eng = create_engine(batch_database.render_as_string(hide_password=False), poolclass=NullPool)
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
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def build_object_key(self) -> str:
        return f"invoices/2026/11/{secrets.token_hex(16)}.bin"

    def put(self, key: str, fileobj, length: int, content_type: str) -> None:
        self.objects[key] = fileobj.read()

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[int] = []
        self.fail = False

    async def enqueue_ocr(self, job_id: int) -> None:
        if self.fail:
            raise ServiceUnavailableError("任务队列不可用")
        self.jobs.append(job_id)


def _settings(**overrides) -> Settings:
    base = {
        "environment": "test",
        "database_url": "postgresql://studyocr:test@127.0.0.1:5433/studyocr",
        "redis_url": "redis://:test@127.0.0.1:6380/0",
        "minio_endpoint": "127.0.0.1:9002",
        "minio_access_key": "test",
        "minio_secret_key": "test",
        "minio_bucket": "study-ocr-test",
        "jwt_secret": "test_secret_" + "x" * 32,
        "scheduler_enabled": False,
        "storage_allow_plaintext": True,
    }
    return Settings(_env_file=None, **{**base, **overrides})


@pytest.fixture()
def env(session_factory, clean_db):
    storage = FakeStorage()
    queue = FakeQueue()
    settings = _settings()
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
    app.dependency_overrides[get_task_queue] = lambda: queue

    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, storage=storage, queue=queue,
                              settings=settings, session_factory=session_factory)


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(user_id=user.id, role=user.role,
                            token_type=TokenType.ACCESS, ttl_seconds=60)
    return {"Authorization": f"Bearer {token}"}


def _jpeg(tag: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40 + tag % 20, 30), (tag % 255, 120, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _post_batch(env, user: User, payloads: list[tuple[str, bytes, str]]):
    return env.client.post(
        f"{BASE}/batch",
        headers=_auth(user),
        files=[("files", (name, data, mime)) for name, data, mime in payloads],
    )


def test_batch_creates_all_and_dispatches_only_three(env, actors) -> None:
    response = _post_batch(env, actors["alice"],
                           [(f"{i}.jpg", _jpeg(i), "image/jpeg") for i in range(5)])

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["created"] == 5
    assert len(data["items"]) == 5
    batch_id = data["batch_id"]

    with env.session_factory() as db:
        invoices = db.scalars(select(Invoice).where(Invoice.batch_id == batch_id)).all()
        assert len(invoices) == 5
        jobs = db.scalars(select(OcrJob).where(
            OcrJob.invoice_id.in_([invoice.id for invoice in invoices]))).all()
        assert len(jobs) == 5
        assert sum(1 for job in jobs if job.dispatched) == 3
    assert len(env.queue.jobs) == 3

    summary = env.client.get(f"{BASE}/batch/{batch_id}", headers=_auth(actors["alice"]))
    assert summary.status_code == 200
    payload = summary.json()["data"]
    assert payload["total"] == 5
    assert payload["counts"]["pending"] == 5


def test_batch_dedup_and_reject_are_per_item(env, actors) -> None:
    same = _jpeg(7)
    response = _post_batch(env, actors["alice"], [
        ("a.jpg", same, "image/jpeg"),
        ("b.jpg", same, "image/jpeg"),
        ("c.txt", b"not an image", "text/plain"),
        ("d.jpg", _jpeg(8), "image/jpeg"),
    ])

    assert response.status_code == 202
    data = response.json()["data"]
    assert (data["created"], data["duplicates"], data["rejected"]) == (2, 1, 1)
    statuses = [item["status"] for item in data["items"]]
    assert statuses.count("duplicate") == 1
    assert statuses.count("rejected") == 1


def test_get_batch_dispatches_next_when_slot_frees(env, actors) -> None:
    response = _post_batch(env, actors["alice"],
                           [(f"{i}.jpg", _jpeg(i), "image/jpeg") for i in range(5)])
    batch_id = response.json()["data"]["batch_id"]
    assert len(env.queue.jobs) == 3

    with env.session_factory() as db:
        first = db.scalar(select(OcrJob).where(OcrJob.dispatched.is_(True)).order_by(OcrJob.id))
        first.status = "succeeded"
        db.commit()

    env.client.get(f"{BASE}/batch/{batch_id}", headers=_auth(actors["alice"]))

    assert len(env.queue.jobs) == 4          # 空出一个槽，补位 1 个
    with env.session_factory() as db:
        assert db.scalar(select(OcrJob).where(OcrJob.dispatched.is_(True), OcrJob.status == "pending")) is not None


def test_retry_does_not_create_new_invoice(env, actors) -> None:
    response = _post_batch(env, actors["alice"],
                           [("a.jpg", _jpeg(1), "image/jpeg"), ("b.jpg", _jpeg(2), "image/jpeg")])
    batch_id = response.json()["data"]["batch_id"]
    with env.session_factory() as db:
        invoice = db.scalar(select(Invoice).where(Invoice.batch_id == batch_id).order_by(Invoice.id))
        invoice_id = invoice.id
        db.execute(sa_text("update ocr_jobs set status='failed' where invoice_id=:id"), {"id": invoice_id})
        db.execute(sa_text("update invoices set ocr_status='failed' where id=:id"), {"id": invoice_id})
        db.commit()

    retried = env.client.post(f"{BASE}/batch/{batch_id}/items/{invoice_id}/retry",
                              headers=_auth(actors["alice"]))

    assert retried.status_code == 202, retried.text
    assert retried.json()["data"]["invoice_id"] == invoice_id
    with env.session_factory() as db:
        assert db.scalar(select(Invoice).where(Invoice.batch_id == batch_id)) is not None
        assert len(db.scalars(select(Invoice).where(Invoice.batch_id == batch_id)).all()) == 2
        jobs = db.scalars(select(OcrJob).where(OcrJob.invoice_id == invoice_id)).all()
        assert len(jobs) == 2                 # 新 job，不新建 invoice


def test_retry_rejects_active_job_and_wrong_batch(env, actors) -> None:
    response = _post_batch(env, actors["alice"], [("a.jpg", _jpeg(1), "image/jpeg")])
    batch_id = response.json()["data"]["batch_id"]
    invoice_id = response.json()["data"]["items"][0]["invoice_id"]

    active = env.client.post(f"{BASE}/batch/{batch_id}/items/{invoice_id}/retry",
                             headers=_auth(actors["alice"]))
    assert active.status_code == 409

    wrong = env.client.post(f"{BASE}/batch/{uuid4()}/items/{invoice_id}/retry",
                            headers=_auth(actors["alice"]))
    assert wrong.status_code == 404

    other = env.client.post(f"{BASE}/batch/{batch_id}/items/{invoice_id}/retry",
                            headers=_auth(actors["bob"]))
    assert other.status_code == 404


def test_batch_summary_permissions_and_bad_uuid(env, actors) -> None:
    response = _post_batch(env, actors["alice"], [("a.jpg", _jpeg(1), "image/jpeg")])
    batch_id = response.json()["data"]["batch_id"]

    assert env.client.get(f"{BASE}/batch/{batch_id}",
                          headers=_auth(actors["rita"])).status_code == 200
    assert env.client.get(f"{BASE}/batch/{batch_id}",
                          headers=_auth(actors["bob"])).status_code == 404
    assert env.client.get(f"{BASE}/batch/not-a-uuid",
                          headers=_auth(actors["alice"])).status_code == 422


def test_thirty_files_reach_hundred_percent(env, actors) -> None:
    payloads = [(f"{i}.jpg", _jpeg(i), "image/jpeg") for i in range(30)]
    response = _post_batch(env, actors["alice"], payloads)

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["created"] == 30
    batch_id = data["batch_id"]
    assert len(env.queue.jobs) == 3

    with env.session_factory() as db:
        db.execute(sa_text(
            "update invoices set ocr_status='succeeded', extract_status='confirmed' "
            "where batch_id=:id"), {"id": batch_id})
        db.execute(sa_text(
            "update ocr_jobs set status='succeeded', dispatched=true "
            "where invoice_id in (select id from invoices where batch_id=:id)"), {"id": batch_id})
        db.commit()

    summary = env.client.get(f"{BASE}/batch/{batch_id}", headers=_auth(actors["alice"]))
    payload = summary.json()["data"]
    assert payload["total"] == 30
    assert payload["counts"]["succeeded"] == 30
    assert all(item["progress"] == 100 for item in payload["items"])