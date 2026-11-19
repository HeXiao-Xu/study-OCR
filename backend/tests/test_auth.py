"""认证接口集成测试：临时 PostgreSQL 库 + 内存 refresh 白名单。

跑法：`cd backend && ../.venv/Scripts/python.exe -m pytest -m integration -q`
不连真实 Redis：`get_token_store` 被替换成内存实现（协议不变，行为一致）。
"""
from __future__ import annotations

import logging
import secrets
from collections.abc import Iterator

import pytest
from alembic import command
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.api.deps import get_token_store, require_roles
from app.core.database import get_db
from app.core.exceptions import AuthError, ok
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.users import User
from app.services.auth_service import authenticate, create_user
from tests.db_helpers import alembic_config, temporary_database

pytestmark = pytest.mark.integration

PASSWORD = "Passw0rd!2026"
BASE = "/api/v1/auth"


class InMemoryRefreshTokenStore:
    """与 RedisRefreshTokenStore 同协议的测试替身。"""

    def __init__(self) -> None:
        self.tokens: dict[str, tuple[str, int]] = {}

    def save(self, jti: str, user_id: str, ttl_seconds: int) -> None:
        self.tokens[jti] = (user_id, ttl_seconds)

    def get(self, jti: str) -> str | None:
        entry = self.tokens.get(jti)
        return entry[0] if entry else None

    def delete(self, jti: str) -> None:
        self.tokens.pop(jti, None)


# --------------------------------------------------------------------------- #
# 夹具：临时库 + 每个用例一个干净库/内存白名单/应用
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def auth_database() -> Iterator[URL]:
    with temporary_database("study_ocr_auth_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(auth_database: URL):
    eng = create_engine(
        auth_database.render_as_string(hide_password=False), poolclass=NullPool
    )
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def store() -> InMemoryRefreshTokenStore:
    return InMemoryRefreshTokenStore()


@pytest.fixture()
def client(engine, session_factory, store) -> Iterator[TestClient]:
    # 每个用例前清空 users；CASCADE 会连带清空所有引用 users 的表（auth 用例不写它们）
    with engine.begin() as conn:
        conn.execute(sa_text("TRUNCATE users RESTART IDENTITY CASCADE"))

    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_token_store] = lambda: store

    @app.get("/api/v1/_test/reviewer-only")
    def _reviewer_only(user: User = Depends(require_roles("reviewer"))):  # pragma: no cover
        return ok({"role": user.role, "id": user.id})

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 小助手
# --------------------------------------------------------------------------- #
def _register(
    client: TestClient,
    *,
    username: str = "alice",
    password: str = PASSWORD,
    real_name: str = "张三",
    dept: str | None = None,
):
    body: dict[str, object] = {
        "username": username,
        "password": password,
        "real_name": real_name,
    }
    if dept is not None:
        body["dept"] = dept
    return client.post(f"{BASE}/register", json=body)


def _login(client: TestClient, *, username: str = "alice", password: str = PASSWORD):
    return client.post(f"{BASE}/login", json={"username": username, "password": password})


def _login_data(client: TestClient, **kwargs) -> dict:
    response = _login(client, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_user(session_factory, *, username: str = "reviewer1", role: str = "reviewer") -> int:
    with session_factory() as db:
        user = create_user(
            db, username=username, password=PASSWORD, real_name="审核员", role=role
        )
        return user.id


def _update_user(session_factory, username: str, **values) -> None:
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    with session_factory() as db:
        db.execute(
            sa_text(f"update users set {assignments} where username = :username"),
            {**values, "username": username},
        )
        db.commit()


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
def test_register_creates_submitter(client: TestClient) -> None:
    response = _register(client, dept="计算机学院")
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["username"] == "alice"
    assert data["role"] == "submitter"
    assert data["status"] == "active"
    assert data["dept"] == "计算机学院"
    assert data["created_at"]
    assert "password_hash" not in data
    assert response.headers.get("X-Trace-Id")


def test_register_trims_and_lowercases_username(client: TestClient) -> None:
    response = _register(client, username="  Alice  ")
    assert response.status_code == 201
    assert response.json()["data"]["username"] == "alice"
    assert _login(client, username="ALICE").status_code == 200


def test_register_duplicate_username_conflicts(client: TestClient) -> None:
    assert _register(client).status_code == 201
    response = _register(client, username="ALICE")
    assert response.status_code == 409
    assert response.json()["code"] == 40900


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password", "short"),
        ("username", "a b"),
        ("username", "ab"),
        ("real_name", "   "),
    ],
)
def test_register_validation_errors(client: TestClient, field: str, value: str) -> None:
    body = {"username": "alice", "password": PASSWORD, "real_name": "张三"}
    body[field] = value
    response = client.post(f"{BASE}/register", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == 42200


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
def test_login_returns_contract_fields(client: TestClient) -> None:
    _register(client)
    response = _login(client)
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "refresh_expires_in",
        "user",
    }
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 1800
    assert data["refresh_expires_in"] == 604800
    assert data["user"]["username"] == "alice"


def test_login_unknown_user_and_wrong_password_are_indistinguishable(
    client: TestClient,
) -> None:
    _register(client)
    unknown = _login(client, username="nobody")
    wrong = _login(client, password="WrongPassw0rd!")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["code"] == wrong.json()["code"] == 40101
    assert unknown.json()["message"] == wrong.json()["message"]


@pytest.mark.parametrize("status", ["disabled", "locked"])
def test_login_inactive_account_returns_403(
    client: TestClient, session_factory, status: str
) -> None:
    _register(client)
    _update_user(session_factory, "alice", status=status)
    response = _login(client)
    assert response.status_code == 403
    assert response.json()["code"] == 40301


def test_login_sso_user_without_password_hash_returns_401(
    client: TestClient, session_factory
) -> None:
    _register(client)
    _update_user(session_factory, "alice", password_hash=None)
    response = _login(client)
    assert response.status_code == 401
    assert response.json()["code"] == 40101


# --------------------------------------------------------------------------- #
# me
# --------------------------------------------------------------------------- #
def test_me_returns_current_user(client: TestClient) -> None:
    _register(client)
    tokens = _login_data(client)
    response = client.get(f"{BASE}/me", headers=_auth(tokens["access_token"]))
    assert response.status_code == 200
    assert response.json()["data"]["username"] == "alice"


def test_me_without_token_returns_401(client: TestClient) -> None:
    response = client.get(f"{BASE}/me")
    assert response.status_code == 401
    assert response.json()["code"] == 40101


def test_me_rejects_refresh_token_as_bearer(client: TestClient) -> None:
    _register(client)
    tokens = _login_data(client)
    response = client.get(f"{BASE}/me", headers=_auth(tokens["refresh_token"]))
    assert response.status_code == 401


def test_me_rejects_expired_access_token(client: TestClient) -> None:
    _register(client)
    user_id = _login_data(client)["user"]["id"]
    expired, _ = encode_token(
        user_id=user_id,
        role="submitter",
        token_type=TokenType.ACCESS,
        ttl_seconds=-1,
    )
    response = client.get(f"{BASE}/me", headers=_auth(expired))
    assert response.status_code == 401


def test_me_unknown_user_returns_404(client: TestClient) -> None:
    ghost, _ = encode_token(
        user_id=999999,
        role="submitter",
        token_type=TokenType.ACCESS,
        ttl_seconds=60,
    )
    response = client.get(f"{BASE}/me", headers=_auth(ghost))
    assert response.status_code == 404
    assert response.json()["code"] == 40400


def test_me_ignores_role_claim_and_uses_database(client: TestClient) -> None:
    _register(client)
    user_id = _login_data(client)["user"]["id"]
    # 令牌里写 admin，但库里是 submitter
    forged_role, _ = encode_token(
        user_id=user_id,
        role="admin",
        token_type=TokenType.ACCESS,
        ttl_seconds=60,
    )
    response = client.get(f"{BASE}/me", headers=_auth(forged_role))
    assert response.status_code == 200
    assert response.json()["data"]["role"] == "submitter"


def test_me_disabled_user_returns_401(client: TestClient, session_factory) -> None:
    _register(client)
    tokens = _login_data(client)
    _update_user(session_factory, "alice", status="disabled")
    response = client.get(f"{BASE}/me", headers=_auth(tokens["access_token"]))
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# refresh / logout
# --------------------------------------------------------------------------- #
def test_refresh_rotates_and_invalidates_old_token(client: TestClient) -> None:
    _register(client)
    first = _login_data(client)
    rotated = client.post(f"{BASE}/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    second = rotated.json()["data"]
    assert "user" not in second
    assert second["refresh_token"] != first["refresh_token"]

    reuse = client.post(f"{BASE}/refresh", json={"refresh_token": first["refresh_token"]})
    assert reuse.status_code == 401

    again = client.post(f"{BASE}/refresh", json={"refresh_token": second["refresh_token"]})
    assert again.status_code == 200


def test_refresh_rejects_access_token(client: TestClient) -> None:
    _register(client)
    tokens = _login_data(client)
    response = client.post(f"{BASE}/refresh", json={"refresh_token": tokens["access_token"]})
    assert response.status_code == 401


def test_logout_revokes_refresh_token(client: TestClient) -> None:
    _register(client)
    tokens = _login_data(client)
    response = client.post(
        f"{BASE}/logout",
        headers=_auth(tokens["access_token"]),
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"logged_out": True}

    reuse = client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401


def test_logout_is_idempotent_and_requires_login(client: TestClient) -> None:
    _register(client)
    tokens = _login_data(client)
    body = {"refresh_token": tokens["refresh_token"]}
    assert (
        client.post(f"{BASE}/logout", headers=_auth(tokens["access_token"]), json=body).status_code
        == 200
    )
    assert (
        client.post(f"{BASE}/logout", headers=_auth(tokens["access_token"]), json=body).status_code
        == 200
    )
    assert client.post(f"{BASE}/logout", json=body).status_code == 401


def test_logout_without_refresh_body_keeps_refresh_valid(client: TestClient) -> None:
    """契约允许只带 access 登出；此时 refresh 不会被吊销——把现状钉住，避免误以为已吊销。"""
    _register(client)
    tokens = _login_data(client)
    assert (
        client.post(f"{BASE}/logout", headers=_auth(tokens["access_token"]), json={}).status_code
        == 200
    )
    response = client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200


def test_access_token_survives_logout_within_ttl(client: TestClient) -> None:
    """D-039 的边界：登出只吊销 refresh，access 在剩余 30 分钟内仍可用。

    这条不是 bug，而是无状态 JWT 的固有代价；用测试钉住，避免有人误以为登出立即失效。
    """
    _register(client)
    tokens = _login_data(client)
    client.post(
        f"{BASE}/logout",
        headers=_auth(tokens["access_token"]),
        json={"refresh_token": tokens["refresh_token"]},
    )
    response = client.get(f"{BASE}/me", headers=_auth(tokens["access_token"]))
    assert response.status_code == 200


def test_multiple_devices_get_independent_sessions(client: TestClient, store) -> None:
    _register(client)
    first = _login_data(client)
    second = _login_data(client)
    assert first["refresh_token"] != second["refresh_token"]
    assert len(store.tokens) == 2

    rotated = client.post(f"{BASE}/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    # 轮换第一个会话不影响第二个
    still_valid = client.post(f"{BASE}/refresh", json={"refresh_token": second["refresh_token"]})
    assert still_valid.status_code == 200


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #
def test_require_roles_rejects_submitter_and_allows_reviewer(
    client: TestClient, session_factory
) -> None:
    _register(client)
    submitter = _login_data(client)
    denied = client.get("/api/v1/_test/reviewer-only", headers=_auth(submitter["access_token"]))
    assert denied.status_code == 403
    assert denied.json()["code"] == 40301

    _create_user(session_factory, username="reviewer1", role="reviewer")
    reviewer = _login_data(client, username="reviewer1")
    allowed = client.get("/api/v1/_test/reviewer-only", headers=_auth(reviewer["access_token"]))
    assert allowed.status_code == 200
    assert allowed.json()["data"]["role"] == "reviewer"


# --------------------------------------------------------------------------- #
# 日志脱敏
# --------------------------------------------------------------------------- #
def test_failed_login_logs_hash_not_password(
    session_factory, caplog: pytest.LogCaptureFixture
) -> None:
    # 本用例不建 client（不做 TRUNCATE），用唯一用户名避免与其他用例的残留数据冲突
    username = f"alice_{secrets.token_hex(3)}"
    with session_factory() as db:
        create_user(db, username=username, password=PASSWORD, real_name="张三")
        with caplog.at_level(logging.WARNING, logger="app.auth"):
            with pytest.raises(AuthError):
                authenticate(db, username, "WrongPassw0rd!")

    assert any(getattr(record, "username_sha256", None) for record in caplog.records)
    for record in caplog.records:
        assert "WrongPassw0rd!" not in record.getMessage()
        assert "WrongPassw0rd!" not in str(record.__dict__)