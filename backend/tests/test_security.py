"""认证密码学原语的单元测试（不连数据库/Redis，默认 `pytest` 就跑）。

覆盖：Argon2id 哈希与校验、JWT 签发/解码、过期、篡改、算法混淆、令牌类型混用、用户名规范化。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import get_settings
from app.core.exceptions import AuthError
from app.core.security import (
    TokenType,
    decode_token,
    dummy_verify,
    encode_token,
    hash_password,
    verify_password,
)
from app.utils.text import normalize_username, username_error

PASSWORD = "Passw0rd!2026"


def test_hash_password_is_argon2id_with_configured_cost() -> None:
    settings = get_settings()
    digest = hash_password(PASSWORD)
    assert digest.startswith("$argon2id$")
    assert PASSWORD not in digest
    # 参数必须真的生效（memory_cost 是登录耗时的隐藏瓶颈，D-038）
    assert (
        f"m={settings.argon2_memory_cost},t={settings.argon2_time_cost},p={settings.argon2_parallelism}"
        in digest
    )


def test_verify_password_roundtrip_and_wrong_password() -> None:
    digest = hash_password(PASSWORD)
    assert verify_password(PASSWORD, digest) is True
    assert verify_password(PASSWORD + "x", digest) is False


def test_verify_password_handles_empty_and_garbage() -> None:
    assert verify_password(PASSWORD, None) is False
    assert verify_password(PASSWORD, "") is False
    assert verify_password(PASSWORD, "not-a-hash") is False


def test_hash_is_salted() -> None:
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_dummy_verify_consumes_time_without_raising() -> None:
    started = time.perf_counter()
    dummy_verify(PASSWORD)
    assert time.perf_counter() - started >= 0


def test_encode_decode_roundtrip() -> None:
    token, jti = encode_token(
        user_id=42,
        role="reviewer",
        token_type=TokenType.ACCESS,
        ttl_seconds=60,
    )
    payload = decode_token(token, expected_type=TokenType.ACCESS)
    assert (payload.sub, payload.role, payload.jti, payload.typ) == (42, "reviewer", jti, "access")
    assert payload.exp > payload.iat


def test_each_token_gets_unique_jti() -> None:
    first, first_jti = encode_token(
        user_id=1, role="submitter", token_type=TokenType.ACCESS, ttl_seconds=60
    )
    second, second_jti = encode_token(
        user_id=1, role="submitter", token_type=TokenType.ACCESS, ttl_seconds=60
    )
    assert first != second
    assert first_jti != second_jti


def test_expired_token_rejected() -> None:
    token, _ = encode_token(
        user_id=1, role="submitter", token_type=TokenType.ACCESS, ttl_seconds=-1
    )
    with pytest.raises(AuthError):
        decode_token(token, expected_type=TokenType.ACCESS)


def test_tampered_token_rejected() -> None:
    token, _ = encode_token(
        user_id=1, role="submitter", token_type=TokenType.ACCESS, ttl_seconds=60
    )
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(AuthError):
        decode_token(tampered, expected_type=TokenType.ACCESS)


def test_alg_none_token_rejected() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "1",
        "role": "admin",
        "jti": "x" * 32,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    forged = jwt.encode(payload, key="", algorithm="none")
    with pytest.raises(AuthError):
        decode_token(forged, expected_type=TokenType.ACCESS)


def test_token_type_confusion_rejected() -> None:
    settings = get_settings()
    access, _ = encode_token(
        user_id=1, role="submitter", token_type=TokenType.ACCESS, ttl_seconds=60
    )
    refresh, _ = encode_token(
        user_id=1, role="submitter", token_type=TokenType.REFRESH, ttl_seconds=60
    )
    with pytest.raises(AuthError):
        decode_token(access, expected_type=TokenType.REFRESH)
    with pytest.raises(AuthError):
        decode_token(refresh, expected_type=TokenType.ACCESS)


def test_missing_typ_claim_rejected() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "1",
        "role": "admin",
        "jti": "y" * 32,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(AuthError):
        decode_token(token, expected_type=TokenType.ACCESS)


def test_token_signed_with_other_secret_rejected() -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "1",
        "role": "admin",
        "jti": "z" * 32,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(payload, "another-secret-" + "x" * 32, algorithm="HS256")
    with pytest.raises(AuthError):
        decode_token(token, expected_type=TokenType.ACCESS)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" Alice ", "alice"), ("BOB", "bob"), ("  张san ", "张san")],
)
def test_normalize_username(raw: str, expected: str) -> None:
    assert normalize_username(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "  ab  ", "a b", "a" * 65],
)
def test_username_error_rejects_invalid(raw: str) -> None:
    assert username_error(raw) is not None


@pytest.mark.parametrize("raw", ["abc", "alice", "a" * 64])
def test_username_error_accepts_valid(raw: str) -> None:
    assert username_error(raw) is None