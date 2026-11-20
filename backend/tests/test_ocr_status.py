"""`GET /invoices/{id}/ocr-status` 测试（S3-1）。

进度权重是**契约的一部分**（API.md §2：preprocess 10 / detect 30 / recognize 40 / extract 20），
所以权重与累加规则在这里用测试钉死；接口读的是 `ocr_jobs.metrics.stages` 的真实数据，
没有 metrics 的历史作业必须优雅退化，不能 500。
"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import datetime, timezone

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
from app.models.invoice_files import InvoiceFile
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.services import invoice_service
from app.services.ocr_status_service import STAGE_WEIGHTS, compute_progress
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/invoices"


# --------------------------------------------------------------------------- #
# 单元：进度权重
# --------------------------------------------------------------------------- #
def test_compute_progress_accumulates_completed_stage_weights() -> None:
    assert compute_progress([{"stage": "preprocess", "status": "succeeded"}], "running") == 10
    stages = [
        {"stage": "preprocess", "status": "succeeded"},
        {"stage": "detect", "status": "succeeded"},
        {"stage": "recognize", "status": "succeeded"},
    ]
    assert compute_progress(stages, "running") == 80


def test_compute_progress_succeeded_is_100() -> None:
    assert compute_progress([], "succeeded") == 100


def test_compute_progress_failed_keeps_completed_weight() -> None:
    stages = [
        {"stage": "preprocess", "status": "succeeded"},
        {"stage": "recognize", "status": "failed"},
    ]
    assert compute_progress(stages, "failed") == 10


def test_stage_weights_match_api_contract() -> None:
    assert STAGE_WEIGHTS == {"preprocess": 10, "detect": 30, "recognize": 40, "extract": 20}


# --------------------------------------------------------------------------- #
# 集成：临时库 + HTTP
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def status_database() -> Iterator[URL]:
    with temporary_database("study_ocr_ocrst_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(status_database: URL):
    eng = create_engine(status_database.render_as_string(hide_password=False), poolclass=NullPool)
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


def _make_invoice(session_factory, uploader_id: int) -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin")
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=uploader_id, file_id=file.id)
        db.commit()
        return invoice.id


def _add_job(session_factory, invoice_id: int, *, status: str, metrics: dict | None,
             ocr_status: str, started: bool = True, error: str | None = None) -> None:
    with session_factory() as db:
        db.execute(
            sa_text("update invoices set ocr_status = :status, invoice_type = 'vat_digital'"
                    " where id = :id"),
            {"status": ocr_status, "id": invoice_id},
        )
        db.add(OcrJob(
            invoice_id=invoice_id, stage="extract", status=status, engine="paddleocr",
            engine_version="2.7.3", retry_count=1, elapsed_ms=1820,
            error=error,
            started_at=datetime.now(timezone.utc) if started else None,
            metrics=metrics,
        ))
        db.commit()


@pytest.mark.integration
def test_ocr_status_owner_reads_stages_and_progress(client, session_factory, actors) -> None:
    metrics = {"version": 1, "stages": [
        {"stage": "preprocess", "status": "succeeded", "elapsed_ms": 80},
        {"stage": "detect", "status": "succeeded", "elapsed_ms": None},
        {"stage": "recognize", "status": "succeeded", "elapsed_ms": 1400},
    ]}
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _add_job(session_factory, invoice_id, status="running", metrics=metrics, ocr_status="running")

    response = client.get(f"{BASE}/{invoice_id}/ocr-status", headers=_auth(actors["alice"]))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["invoice_id"] == invoice_id
    assert data["ocr_status"] == "running"
    assert data["progress"] == 80
    assert [stage["stage"] for stage in data["stages"]] == ["preprocess", "detect", "recognize"]
    assert data["stages"][1]["elapsed_ms"] is None
    assert data["stages"][0]["engine_version"] == "2.7.3"
    assert data["started_at"] is not None and data["finished_at"] is None


@pytest.mark.integration
def test_ocr_status_failed_hangs_error_on_failing_stage(client, session_factory, actors) -> None:
    metrics = {"version": 1, "stages": [
        {"stage": "preprocess", "status": "succeeded", "elapsed_ms": 80},
        {"stage": "recognize", "status": "failed", "elapsed_ms": None},
    ]}
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _add_job(session_factory, invoice_id, status="failed", metrics=metrics,
             ocr_status="failed", error="OcrContractError: paddleocr 版本不符")

    data = client.get(f"{BASE}/{invoice_id}/ocr-status",
                      headers=_auth(actors["alice"])).json()["data"]

    assert data["ocr_status"] == "failed"
    assert data["progress"] == 10
    assert [stage["error"] for stage in data["stages"]] == [None, "OcrContractError: paddleocr 版本不符"]


@pytest.mark.integration
def test_ocr_status_reviewer_can_read_others(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _add_job(session_factory, invoice_id, status="running", metrics=None, ocr_status="running")

    assert client.get(f"{BASE}/{invoice_id}/ocr-status",
                      headers=_auth(actors["rita"])).status_code == 200


@pytest.mark.integration
def test_ocr_status_other_submitter_gets_404(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)

    assert client.get(f"{BASE}/{invoice_id}/ocr-status",
                      headers=_auth(actors["bob"])).status_code == 404


@pytest.mark.integration
def test_ocr_status_admin_gets_404(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)

    assert client.get(f"{BASE}/{invoice_id}/ocr-status",
                      headers=_auth(actors["ada"])).status_code == 404


@pytest.mark.integration
def test_ocr_status_without_metrics_degrades(client, session_factory, actors) -> None:
    """历史作业没有 metrics（S3-1 之前）：单条阶段 + 不报 500。"""
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _add_job(session_factory, invoice_id, status="succeeded", metrics=None,
             ocr_status="succeeded")

    data = client.get(f"{BASE}/{invoice_id}/ocr-status",
                      headers=_auth(actors["alice"])).json()["data"]

    assert data["progress"] == 100
    assert len(data["stages"]) == 1
    assert data["stages"][0]["stage"] == "extract"


@pytest.mark.integration
def test_ocr_status_without_job_returns_empty_structure(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)

    data = client.get(f"{BASE}/{invoice_id}/ocr-status",
                      headers=_auth(actors["alice"])).json()["data"]

    assert data["stages"] == [] and data["progress"] == 0
    assert data["started_at"] is None and data["finished_at"] is None