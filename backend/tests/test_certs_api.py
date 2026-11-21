"""证书 API（S4-6 补全）：列表权限 / 吊销 / 幂等 / 审计（集成）。"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models.certificates import Certificate
from app.models.users import User
from app.services.audit import verify_chain
from tests.db_helpers import alembic_config, temporary_database
from tests.test_submit import (
    FakeStorage,
    _auth,
    _client_for,
    _register_identities,
    sm_identities,  # noqa: F401 - fixture
)

BASE = "/api/v1/certs"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def cert_database() -> Iterator[URL]:
    with temporary_database("study_ocr_certapi_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(cert_database: URL):
    eng = create_engine(
        cert_database.render_as_string(hide_password=False), poolclass=NullPool
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
            "alice": User(username="alice", real_name="A", role="submitter", status="active"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


@pytest.fixture(autouse=True)
def registered(session_factory, actors, sm_identities) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")


@pytest.fixture()
def client(session_factory) -> Iterator[TestClient]:
    with _client_for(session_factory, FakeStorage()) as test_client:
        yield test_client


def test_list_certificates_permissions(client, actors) -> None:
    as_admin = client.get(BASE, headers=_auth(actors["ada"]))
    assert as_admin.status_code == 200, as_admin.text
    assert as_admin.json()["data"]["total"] == 2  # alice(user) + finance(server)

    server_only = client.get(
        BASE, params={"owner_type": "server"}, headers=_auth(actors["ada"])
    )
    assert server_only.json()["data"]["total"] == 1

    as_alice = client.get(BASE, headers=_auth(actors["alice"]))
    assert as_alice.status_code == 200
    items = as_alice.json()["data"]["items"]
    assert len(items) == 1 and items[0]["owner_type"] == "user"

    forbidden = client.get(
        BASE, params={"owner_type": "server"}, headers=_auth(actors["alice"])
    )
    assert forbidden.status_code == 403


def test_revoke_is_idempotent_and_audited(client, session_factory, actors) -> None:
    cert_id = client.get(BASE, headers=_auth(actors["ada"])).json()["data"]["items"][0]["id"]

    first = client.post(
        f"{BASE}/{cert_id}/revoke",
        json={"reason": "私钥疑似泄露"},
        headers=_auth(actors["ada"]),
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["revoked"] is True
    assert first.json()["data"]["revoked_at"]

    second = client.post(
        f"{BASE}/{cert_id}/revoke",
        json={"reason": "重复调用"},
        headers=_auth(actors["ada"]),
    )
    assert second.status_code == 200

    with session_factory() as db:
        assert db.get(Certificate, cert_id).revoked is True
        audits = db.execute(
            sa_text("select count(*) from audit_logs where action = 'revoke_cert'")
        ).scalar_one()
        assert audits == 1  # 幂等：第二次不再写审计
        assert verify_chain(db).ok is True

    assert (
        client.post(
            f"{BASE}/{cert_id}/revoke",
            json={"reason": "无权"},
            headers=_auth(actors["alice"]),
        ).status_code
        == 403
    )


def test_revoke_requires_reason(client, actors) -> None:
    cert_id = client.get(BASE, headers=_auth(actors["ada"])).json()["data"]["items"][0]["id"]
    response = client.post(
        f"{BASE}/{cert_id}/revoke", json={"reason": ""}, headers=_auth(actors["ada"])
    )
    assert response.status_code == 422
    assert response.json()["code"] == 42200