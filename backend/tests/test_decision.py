"""S5-5 审核决策与驳回：approve 闸门 / verify_token 一次性 / reject 留痕 / 并发（集成）。

装置复用 `test_submit.py`（SM 身份、FakeStorage、真提交链路）与 `test_verify.py` 的做法：
Redis 用 `InMemoryCredentialStore` 注入；财务私钥口令用 settings 覆盖。
"""
from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.exceptions import AppError
from app.models.report_forms import ReportForm
from app.models.review_logs import ReviewLog
from app.models.signatures import Signature
from app.models.users import User
from app.schemas.finance import ReviewRequest
from app.services import review_service
from app.services.audit import verify_chain
from app.services.credential_store import InMemoryCredentialStore, verify_key
from tests.db_helpers import alembic_config, temporary_database
from tests.test_submit import (
    _auth,
    _client_for,
    _register_identities,
    _report_with_item,
    _sign,
    _submit,
    sm_identities,  # noqa: F401 - fixture
)

BASE = "/api/v1/finance"
FINANCE_PASSPHRASE = "test-pass-1234"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def decision_database() -> Iterator[URL]:
    with temporary_database("study_ocr_decision_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(decision_database: URL):
    eng = create_engine(
        decision_database.render_as_string(hide_password=False), poolclass=NullPool
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
        conn.execute(
            sa_text("TRUNCATE users, certificates, key_records RESTART IDENTITY CASCADE")
        )
    yield


@pytest.fixture()
def actors(session_factory, clean_db) -> dict[str, User]:
    with session_factory() as db:
        users = {
            "alice": User(username="alice", real_name="A", role="submitter",
                          status="active", dept="计算机学院"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


@pytest.fixture()
def credential_store() -> InMemoryCredentialStore:
    return InMemoryCredentialStore()


@pytest.fixture()
def storage():
    from tests.test_submit import FakeStorage

    return FakeStorage()


@pytest.fixture()
def client(session_factory, storage, credential_store) -> Iterator[TestClient]:
    with _client_for(
        session_factory,
        storage,
        settings_updates={"finance_key_passphrase": FINANCE_PASSPHRASE},
        credential_store=credential_store,
    ) as test_client:
        yield test_client


@pytest.fixture()
def submitted(client, session_factory, storage, actors, sm_identities) -> int:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_id, _ = _report_with_item(client, actors, session_factory, storage)
    assert _sign(client, actors["alice"], report_id).status_code == 200
    assert _submit(client, actors["alice"], report_id, "idem-decision-0001").status_code == 200
    return report_id


def _version(session_factory, report_id: int) -> int:
    with session_factory() as db:
        return db.scalar(select(ReportForm.version).where(ReportForm.id == report_id))


def _verify(client, actors, report_id: int) -> str:
    response = client.post(
        f"{BASE}/reports/{report_id}/verify", headers=_auth(actors["rita"])
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["verify_token"]


def _review(client, actors, report_id: int, payload: dict):
    return client.post(
        f"{BASE}/reports/{report_id}/review", json=payload, headers=_auth(actors["rita"])
    )


def _approve(client, actors, session_factory, report_id: int, *, token: str | None = None):
    token = token if token is not None else _verify(client, actors, report_id)
    return _review(
        client,
        actors,
        report_id,
        {
            "decision": "approve",
            "version": _version(session_factory, report_id),
            "verify_token": token,
        },
    )


# --------------------------------------------------------------------------- #
# approve
# --------------------------------------------------------------------------- #
def test_approve_success_consumes_token_and_logs(
    client, session_factory, actors, credential_store, submitted
) -> None:
    token = _verify(client, actors, submitted)

    response = _approve(client, actors, session_factory, submitted, token=token)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "approved"
    assert data["decision"] == "approve"
    assert data["reviewed_at"]

    # 一次性消费
    assert credential_store.get(verify_key(submitted)) is None

    with session_factory() as db:
        logs = db.scalars(select(ReviewLog)).all()
        assert len(logs) == 1
        log = logs[0]
        assert (log.form_id, log.reviewer_id) == (submitted, actors["rita"].id)
        assert (log.decision, log.verify_result, log.decrypt_ok) == ("approve", "ok", True)
        signature = db.scalars(
            select(Signature).where(Signature.form_id == submitted, Signature.status == "active")
        ).one()
        assert signature.verify_result == "ok"
        assert verify_chain(db).ok is True

    # 已决策单不再出现在待审列表
    listing = client.get(f"{BASE}/pending", headers=_auth(actors["rita"]))
    assert listing.json()["data"]["total"] == 0

    detail = client.get(f"{BASE}/{submitted}", headers=_auth(actors["rita"]))
    assert detail.status_code == 200
    assert detail.json()["data"]["report"]["status"] == "approved"


def test_approve_without_verify_is_409(
    client, session_factory, actors, submitted
) -> None:
    response = _review(
        client,
        actors,
        submitted,
        {
            "decision": "approve",
            "version": _version(session_factory, submitted),
            "verify_token": "never-issued-token",
        },
    )

    assert response.status_code == 409
    assert response.json()["data"]["reason_code"] == "verify_token_invalid"
    with session_factory() as db:
        assert db.get(ReportForm, submitted).status == "submitted"
        assert db.scalars(select(ReviewLog)).all() == []


def test_approve_wrong_token_is_409(
    client, session_factory, actors, submitted
) -> None:
    _verify(client, actors, submitted)

    response = _review(
        client,
        actors,
        submitted,
        {
            "decision": "approve",
            "version": _version(session_factory, submitted),
            "verify_token": "some-other-token",
        },
    )

    assert response.status_code == 409
    assert response.json()["data"]["reason_code"] == "verify_token_invalid"


def test_approve_token_cannot_be_reused(
    client, session_factory, actors, submitted
) -> None:
    token = _verify(client, actors, submitted)
    assert _approve(client, actors, session_factory, submitted, token=token).status_code == 200

    second = _review(
        client,
        actors,
        submitted,
        {
            "decision": "approve",
            "version": _version(session_factory, submitted),
            "verify_token": token,
        },
    )

    assert second.status_code == 409
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ReviewLog)) == 1


def test_approve_token_cannot_cross_reports(
    client, session_factory, storage, actors, sm_identities
) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    first, _ = _report_with_item(client, actors, session_factory, storage)
    second, _ = _report_with_item(client, actors, session_factory, storage)
    for report_id in (first, second):
        assert _sign(client, actors["alice"], report_id).status_code == 200
    assert _submit(client, actors["alice"], first, "idem-cross-1").status_code == 200
    assert _submit(client, actors["alice"], second, "idem-cross-2").status_code == 200

    token_for_first = _verify(client, actors, first)
    response = _review(
        client,
        actors,
        second,
        {
            "decision": "approve",
            "version": _version(session_factory, second),
            "verify_token": token_for_first,
        },
    )

    assert response.status_code == 409
    with session_factory() as db:
        assert db.get(ReportForm, second).status == "submitted"


# --------------------------------------------------------------------------- #
# reject
# --------------------------------------------------------------------------- #
def test_reject_requires_comment(client, session_factory, actors, submitted) -> None:
    response = _review(
        client,
        actors,
        submitted,
        {"decision": "reject", "version": _version(session_factory, submitted)},
    )

    assert response.status_code == 422
    assert response.json()["code"] == 42200
    with session_factory() as db:
        assert db.get(ReportForm, submitted).status == "submitted"


def test_reject_voids_signature_but_keeps_evidence(
    client, session_factory, actors, submitted
) -> None:
    response = _review(
        client,
        actors,
        submitted,
        {
            "decision": "reject",
            "comment": "票据影像不清晰，请重新上传",
            "version": _version(session_factory, submitted),
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "rejected" and data["decision"] == "reject"

    with session_factory() as db:
        form = db.get(ReportForm, submitted)
        assert form.status == "rejected"
        signature = db.scalars(select(Signature).where(Signature.form_id == submitted)).one()
        # 证据保留：行仍在，只是不再是 active
        assert signature.status == "void"
        log = db.scalars(select(ReviewLog)).one()
        assert log.decision == "reject"
        assert log.comment == "票据影像不清晰，请重新上传"
        assert log.decrypt_ok is True
        assert verify_chain(db).ok is True


def test_duplicate_decision_is_409(client, session_factory, actors, submitted) -> None:
    token = _verify(client, actors, submitted)
    assert _approve(client, actors, session_factory, submitted, token=token).status_code == 200

    second = _review(
        client,
        actors,
        submitted,
        {
            "decision": "reject",
            "comment": "重复决策",
            "version": _version(session_factory, submitted),
        },
    )

    assert second.status_code == 409


def test_version_conflict_is_409(client, session_factory, actors, submitted) -> None:
    token = _verify(client, actors, submitted)

    response = _review(
        client,
        actors,
        submitted,
        {
            "decision": "approve",
            "version": _version(session_factory, submitted) + 1,
            "verify_token": token,
        },
    )

    assert response.status_code == 409
    assert response.json()["data"]["reason_code"] == "version_conflict"


def test_approve_requires_signature_ok(
    client, session_factory, actors, submitted
) -> None:
    """token 有效但签名结论被改成失败（例如 S5-4 已判失败）时，仍不允许通过。"""
    token = _verify(client, actors, submitted)
    with session_factory() as db:
        db.execute(
            sa_text(
                "update signatures set verified = false, verify_result = 'bad_signature'"
                " where form_id = :id and status = 'active'"
            ),
            {"id": submitted},
        )
        db.commit()

    response = _review(
        client,
        actors,
        submitted,
        {
            "decision": "approve",
            "version": _version(session_factory, submitted),
            "verify_token": token,
        },
    )

    assert response.status_code == 409
    assert response.json()["data"]["reason_code"] == "verify_not_ok"
    with session_factory() as db:
        assert db.get(ReportForm, submitted).status == "submitted"
        assert db.scalars(select(ReviewLog)).all() == []


# --------------------------------------------------------------------------- #
# 权限与并发
# --------------------------------------------------------------------------- #
def test_review_permissions(client, session_factory, actors, submitted) -> None:
    payload = {"decision": "reject", "comment": "x", "version": _version(session_factory, submitted)}
    assert client.post(f"{BASE}/reports/{submitted}/review", json=payload).status_code == 401
    for role in ("alice", "ada"):
        response = client.post(
            f"{BASE}/reports/{submitted}/review", json=payload, headers=_auth(actors[role])
        )
        assert response.status_code == 403, (role, response.text)
    assert (
        client.post(
            f"{BASE}/reports/999999/review", json=payload, headers=_auth(actors["rita"])
        ).status_code
        == 404
    )


def test_concurrent_approve_only_one_wins(
    client, session_factory, actors, credential_store, submitted
) -> None:
    token = _verify(client, actors, submitted)
    version = _version(session_factory, submitted)
    barrier = Barrier(2)

    def _decide() -> str:
        db = session_factory()
        try:
            reviewer = db.get(User, actors["rita"].id)
            barrier.wait(timeout=10)
            review_service.decide_report(
                db,
                reviewer,
                submitted,
                ReviewRequest(decision="approve", version=version, verify_token=token),
                store=credential_store,
            )
            return "ok"
        except AppError:
            return "conflict"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _decide(), range(2)))

    assert sorted(results) == ["conflict", "ok"]
    with session_factory() as db:
        assert db.get(ReportForm, submitted).status == "approved"
        assert db.scalar(select(func.count()).select_from(ReviewLog)) == 1