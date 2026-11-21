"""S4-7 审计链集成测试：真实 PostgreSQL + Alembic（`pytest -m integration`）。"""
from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.audit_logs import AuditLog
from app.models.users import User
from app.services import audit
from tests.db_helpers import alembic_config, temporary_database

pytestmark = pytest.mark.integration
BASE = "/api/v1/admin/audit"
NOW = datetime(2026, 11, 21, 9, 0, 0, 654321, tzinfo=timezone.utc)


# ---------------------------------------------------------------- 夹具


@pytest.fixture(scope="module")
def audit_database() -> Iterator[URL]:
    with temporary_database("study_ocr_audit_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(audit_database: URL):
    eng = create_engine(audit_database.render_as_string(hide_password=False), poolclass=NullPool)
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
        conn.execute(sa_text("TRUNCATE audit_logs, users RESTART IDENTITY CASCADE"))
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
    token, _ = encode_token(
        user_id=user.id, role=user.role, token_type=TokenType.ACCESS, ttl_seconds=60
    )
    return {"Authorization": f"Bearer {token}"}


def _append(session_factory, seq: int = 1, **overrides: object) -> int:
    kwargs: dict[str, object] = {
        "action": "upload",
        "trace_id": f"trace-{seq}",
        "actor_role": "system",
        "target_type": "invoice",
        "target_id": seq,
        "detail": {"seq": seq},
        "now": NOW + timedelta(seconds=seq),
    }
    kwargs.update(overrides)
    with session_factory() as db:
        row = audit.append_audit(db, **kwargs)  # type: ignore[arg-type]
        db.commit()
        return int(row.id)


# ---------------------------------------------------------------- 链正确性


def test_append_then_verify_ok(session_factory, clean_db) -> None:
    ids = [_append(session_factory, seq) for seq in (1, 2, 3)]

    with session_factory() as db:
        rows = db.scalars(select(AuditLog).order_by(AuditLog.id.asc())).all()
        result = audit.verify_chain(db)

    assert [row.id for row in rows] == ids
    assert rows[0].prev_hash == audit.GENESIS_HASH
    assert rows[1].prev_hash == rows[0].entry_hash
    assert rows[2].prev_hash == rows[1].entry_hash
    assert result.ok is True
    assert result.checked_count == 3
    assert result.broken_at is None and result.reason is None


def test_tampered_detail_breaks_chain_at_that_row(session_factory, engine, clean_db) -> None:
    ids = [_append(session_factory, seq) for seq in (1, 2, 3)]

    with engine.begin() as conn:
        conn.execute(
            sa_text("UPDATE audit_logs SET detail_json = '{\"seq\": 99}'::jsonb WHERE id = :id"),
            {"id": ids[1]},
        )

    with session_factory() as db:
        result = audit.verify_chain(db)

    assert result.ok is False
    assert result.broken_at == 2
    assert result.broken_id == ids[1]
    assert result.reason == audit.REASON_ENTRY_MISMATCH


def test_tampered_entry_hash_breaks_at_first_bad_row(session_factory, engine, clean_db) -> None:
    ids = [_append(session_factory, seq) for seq in (1, 2, 3)]

    with engine.begin() as conn:
        conn.execute(
            sa_text("UPDATE audit_logs SET entry_hash = :hash WHERE id = :id"),
            {"hash": "e" * 64, "id": ids[0]},
        )

    with session_factory() as db:
        result = audit.verify_chain(db)

    assert result.ok is False
    assert result.broken_at == 1
    assert result.broken_id == ids[0]


def test_deleted_middle_row_breaks_prev_link(session_factory, engine, clean_db) -> None:
    ids = [_append(session_factory, seq) for seq in (1, 2, 3)]

    with engine.begin() as conn:
        conn.execute(sa_text("DELETE FROM audit_logs WHERE id = :id"), {"id": ids[1]})

    with session_factory() as db:
        result = audit.verify_chain(db)

    assert result.ok is False
    assert result.broken_at == 2
    assert result.broken_id == ids[2]
    assert result.reason == audit.REASON_PREV_MISMATCH


def test_rollback_does_not_add_audit_row(session_factory, clean_db) -> None:
    _append(session_factory, 1)

    with session_factory() as db:
        audit.append_audit(
            db,
            action="upload",
            trace_id="rollback-me",
            actor_role="system",
            detail={"seq": 2},
            now=NOW,
        )
        db.rollback()

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert audit.verify_chain(db).ok is True


def test_concurrent_appends_stay_single_chain(session_factory, clean_db) -> None:
    with ThreadPoolExecutor(max_workers=10) as pool:
        ids = list(pool.map(lambda seq: _append(session_factory, seq), range(1, 51)))

    assert len(set(ids)) == 50
    with session_factory() as db:
        result = audit.verify_chain(db)
        distinct_prev = db.scalar(select(func.count(func.distinct(AuditLog.prev_hash))))

    assert result.ok is True
    assert result.checked_count == 50
    assert distinct_prev == 50  # 没有两条共用同一个 prev_hash（不分叉）


# ---------------------------------------------------------------- HTTP（API.md §7）


def test_audit_list_requires_admin(client, actors) -> None:
    assert client.get(BASE, headers=_auth(actors["rita"])).status_code == 403
    assert client.get(BASE, headers=_auth(actors["alice"])).status_code == 403
    assert client.get(BASE, headers=_auth(actors["rita"])).json()["code"] == 40301


def test_audit_list_filters_and_pagination(client, actors, session_factory) -> None:
    _append(session_factory, 1)
    _append(session_factory, 2, action="confirm_field", actor_role="submitter")

    response = client.get(BASE, headers=_auth(actors["ada"]))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert {"id", "action", "entry_hash", "prev_hash", "trace_id", "created_at"} <= set(data["items"][0])

    filtered = client.get(f"{BASE}?action=confirm_field", headers=_auth(actors["ada"]))
    assert filtered.json()["data"]["total"] == 1

    by_trace = client.get(f"{BASE}?trace_id=trace-1", headers=_auth(actors["ada"]))
    assert by_trace.json()["data"]["total"] == 1

    paged = client.get(f"{BASE}?page=2&page_size=1", headers=_auth(actors["ada"]))
    assert paged.json()["data"]["page"] == 2
    assert len(paged.json()["data"]["items"]) == 1


def test_audit_list_rejects_invalid_filters(client, actors, session_factory) -> None:
    _append(session_factory, 1)

    assert client.get(f"{BASE}?action=not_an_action", headers=_auth(actors["ada"])).status_code == 422
    bad_range = "from=2026-11-22T00:00:00%2B00:00&to=2026-11-21T00:00:00%2B00:00"
    assert client.get(f"{BASE}?{bad_range}", headers=_auth(actors["ada"])).status_code == 422
    assert client.get(f"{BASE}?page=0", headers=_auth(actors["ada"])).status_code == 422


def test_verify_chain_endpoint_reports_ok_and_broken(client, actors, session_factory, engine) -> None:
    ids = [_append(session_factory, seq) for seq in (1, 2)]

    response = client.get(f"{BASE}/verify-chain", headers=_auth(actors["ada"]))
    assert response.status_code == 200
    assert response.json()["data"] == {
        "ok": True,
        "checked_count": 2,
        "broken_at": None,
        "broken_id": None,
        "reason": None,
    }

    with engine.begin() as conn:
        conn.execute(
            sa_text("UPDATE audit_logs SET detail_json = '{\"seq\": 99}'::jsonb WHERE id = :id"),
            {"id": ids[1]},
        )

    broken = client.get(f"{BASE}/verify-chain", headers=_auth(actors["ada"])).json()["data"]
    assert broken["ok"] is False
    assert broken["broken_at"] == 2
    assert broken["broken_id"] == ids[1]
    assert broken["reason"] == "entry_hash_mismatch"


def test_verify_chain_endpoint_requires_admin(client, actors, session_factory) -> None:
    _append(session_factory, 1)

    assert client.get(f"{BASE}/verify-chain", headers=_auth(actors["rita"])).status_code == 403