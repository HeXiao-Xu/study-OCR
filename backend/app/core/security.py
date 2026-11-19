"""认证密码学原语：Argon2id 口令哈希 + HS256 JWT。

边界：**不碰数据库、不碰 Redis**。只做"口令 → 哈希 / 校验"与"声明 → 令牌 / 验回来"，
因此可以完全用单元测试覆盖，不依赖任何服务是否启动。

两条硬约束：
- 所有随机量（盐、jti）只用 `secrets`（AGENTS §6 的精神；JWT 的 jti 泄露等于会话可被枚举）；
- 解码时显式 `algorithms=["HS256"]`，防 `alg=none` 与算法混淆攻击。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from functools import lru_cache

import jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthError

_REQUIRED_CLAIMS = ["exp", "iat", "sub", "jti", "typ"]


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class TokenPayload(BaseModel):
    sub: int
    role: str
    jti: str
    typ: str
    iat: int
    exp: int


def _settings(settings: Settings | None) -> Settings:
    return settings or get_settings()


@lru_cache(maxsize=8)
def _password_context(memory_cost: int, time_cost: int, parallelism: int) -> CryptContext:
    # Argon2id 固定 type=ID；参数来自配置，取定依据与实测耗时见 DECISIONS D-038
    return CryptContext(
        schemes=["argon2"],
        argon2__type="ID",
        argon2__memory_cost=memory_cost,
        argon2__time_cost=time_cost,
        argon2__parallelism=parallelism,
    )


def _context(settings: Settings | None = None) -> CryptContext:
    s = _settings(settings)
    return _password_context(s.argon2_memory_cost, s.argon2_time_cost, s.argon2_parallelism)


def hash_password(password: str, *, settings: Settings | None = None) -> str:
    return _context(settings).hash(password)


def verify_password(
    password: str, password_hash: str | None, *, settings: Settings | None = None
) -> bool:
    if not password_hash:
        return False
    try:
        return bool(_context(settings).verify(password, password_hash))
    except (ValueError, TypeError):
        # 库里出现非法哈希（手工改库/迁移残留）按"验证失败"处理，不把异常抛给调用方
        return False


@lru_cache(maxsize=8)
def _dummy_hash(memory_cost: int, time_cost: int, parallelism: int) -> str:
    return _password_context(memory_cost, time_cost, parallelism).hash(secrets.token_urlsafe(16))


def dummy_verify(password: str, *, settings: Settings | None = None) -> None:
    """账号不存在时也消耗一次 Argon2 时间，避免"响应快 = 账号不存在"的枚举侧信道。"""
    s = _settings(settings)
    verify_password(
        password,
        _dummy_hash(s.argon2_memory_cost, s.argon2_time_cost, s.argon2_parallelism),
        settings=settings,
    )


def encode_token(
    *,
    user_id: int,
    role: str,
    token_type: TokenType,
    ttl_seconds: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """签发令牌，返回 `(jwt, jti)`；`jti` 由调用方决定是否写入白名单。"""
    s = _settings(settings)
    issued_at = now or datetime.now(timezone.utc)
    jti = secrets.token_hex(16)
    payload = {
        "sub": str(user_id),
        "role": role,
        "jti": jti,
        "typ": token_type.value,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), jti


def decode_token(
    token: str,
    *,
    expected_type: TokenType,
    settings: Settings | None = None,
) -> TokenPayload:
    """验签 + 校验必填声明 + 比对令牌类型；任何问题统一抛 `AuthError`。"""
    s = _settings(settings)
    try:
        raw = jwt.decode(
            token,
            s.jwt_secret,
            algorithms=[s.jwt_algorithm],
            options={"require": _REQUIRED_CLAIMS},
        )
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise AuthError("令牌无效或已过期") from exc

    try:
        payload = TokenPayload(
            sub=int(raw["sub"]),
            role=str(raw["role"]),
            jti=str(raw["jti"]),
            typ=str(raw["typ"]),
            iat=int(raw["iat"]),
            exp=int(raw["exp"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("令牌无效或已过期") from exc

    if payload.typ != expected_type.value:
        raise AuthError("令牌类型不符")
    return payload