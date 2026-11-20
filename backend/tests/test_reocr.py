"""S3-3 reocr 提交侧：权限、状态、字段校验、重复作业、入队失败补偿。

契约是异步 `POST /invoices/{id}/reocr`（API.md §2）：提交只建作业+入队，
真正的裁剪识别在 worker 里（见 `run_reocr` 用例）。
"""
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
from app.models.enums import FieldSource
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.services import invoice_service
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/invoices"


class FakeQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.queued: list[int] = []
        self.fail = fail

    async def enqueue_ocr(self, job_id: int) -> None:
        await self.enqueue_reocr(job_id)

    async def enqueue_reocr(self, job_id: int) -> None:
        if self.fail:
            raise RuntimeError("queue down")
        self.queued.append(job_id)


# --------------------------------------------------------------------------- #
# 夹具（临时库 + 假队列）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def reocr_database() -> Iterator[URL]:
    with temporary_database("study_ocr_reocr_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(reocr_database: URL):
    eng = create_engine(reocr_database.render_as_string(hide_password=False), poolclass=NullPool)
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
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture()
def client(session_factory, clean_db, fake_queue) -> Iterator[TestClient]:
    from app.api.deps import get_task_queue

    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_task_queue] = lambda: fake_queue
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(user_id=user.id, role=user.role,
                            token_type=TokenType.ACCESS, ttl_seconds=60)
    return {"Authorization": f"Bearer {token}"}


def _make_invoice_with_fields(session_factory, uploader_id: int, **fields) -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin")
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=uploader_id, file_id=file.id)
        db.add(InvoiceField(invoice_id=invoice.id, field_name="total_amount",
                            field_value="100.00", normalized_value="100.00",
                            raw_text="¥100.00", source="ocr", confidence=Decimal("0.700"),
                            bbox_json=[10, 20, 300, 60], source_page_no=1, source_line_no=3))
        db.commit()
        return invoice.id


pytestmark = pytest.mark.integration


def test_reocr_submits_job_and_enqueues(client, session_factory, actors, fake_queue) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)

    response = client.post(f"{BASE}/{invoice_id}/reocr",
                           json={"fields": ["total_amount"]}, headers=_auth(actors["alice"]))

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["invoice_id"] == invoice_id
    assert data["fields"] == ["total_amount"]
    assert fake_queue.queued == [data["job_id"]]
    with session_factory() as db:
        job = db.get(OcrJob, data["job_id"])
        assert job.stage == "recognize"
        assert job.params_json == {"kind": "reocr", "fields": ["total_amount"]}


def test_reocr_other_submitter_gets_404(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)

    response = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                           headers=_auth(actors["bob"]))

    assert response.status_code == 404


def test_reocr_reviewer_forbidden_per_contract(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)

    response = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                           headers=_auth(actors["rita"]))

    assert response.status_code == 403


def test_reocr_rejects_non_draft(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    with session_factory() as db:
        db.execute(sa_text("update invoices set status='confirmed' where id=:id"),
                   {"id": invoice_id})
        db.commit()

    response = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                           headers=_auth(actors["alice"]))

    assert response.status_code == 409


@pytest.mark.parametrize("setup", ["unknown_field", "manual_field", "already_retried", "no_bbox"])
def test_reocr_rejects_invalid_fields(client, session_factory, actors, setup: str) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    with session_factory() as db:
        row = db.scalar(select(InvoiceField).where(InvoiceField.invoice_id == invoice_id))
        if setup == "manual_field":
            row.source = FieldSource.MANUAL.value
        elif setup == "already_retried":
            row.source = FieldSource.OCR_RETRY.value
        elif setup == "no_bbox":
            row.bbox_json = None
        db.commit()

    field = "not_a_field" if setup == "unknown_field" else "total_amount"
    response = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": [field]},
                           headers=_auth(actors["alice"]))

    assert response.status_code == 422


def test_reocr_rejects_duplicate_running_job(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    first = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                        headers=_auth(actors["alice"]))
    assert first.status_code == 202

    second = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                         headers=_auth(actors["alice"]))

    assert second.status_code == 409


def test_reocr_version_conflict(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)

    response = client.post(f"{BASE}/{invoice_id}/reocr",
                           json={"fields": ["total_amount"], "version": 99},
                           headers=_auth(actors["alice"]))

    assert response.status_code == 409


def test_reocr_queue_failure_marks_job_failed(client, session_factory, actors,
                                              fake_queue) -> None:
    fake_queue.fail = True
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)

    response = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                           headers=_auth(actors["alice"]))

    assert response.status_code == 503
    with session_factory() as db:
        job = db.scalar(select(OcrJob).where(OcrJob.invoice_id == invoice_id))
        assert job.status == "failed"
        assert "入队失败" in job.error


@pytest.mark.parametrize(
    "body",
    [
        {"fields": []},
        {"fields": ["a", "b", "c", "d", "e", "f", "g"]},
        {"fields": ["total_amount", "total_amount"]},
    ],
)
def test_reocr_request_validation(client, session_factory, actors, body: dict) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)

    response = client.post(f"{BASE}/{invoice_id}/reocr", json=body,
                           headers=_auth(actors["alice"]))

    assert response.status_code == 422