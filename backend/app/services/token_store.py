"""refresh token 白名单存储（D-016）。

为什么不用无状态黑名单：登出必须**真的能吊销**。refresh token 有效期 7 天，
如果只靠签名校验，登出后令牌仍可换新——这是最常被忽略的一类会话漏洞。
做法：签发 refresh 时把 `jti` 写进 Redis（key `auth:refresh:{jti}`，value=user_id，TTL=7d），
刷新时校验并轮换，登出时删除。

接口做成协议，测试可注入内存实现，不需要 Redis 服务。
"""
from __future__ import annotations

from typing import Protocol

from redis import Redis

from app.core.config import get_settings
from app.core.redis import get_redis


class RefreshTokenStore(Protocol):
    def save(self, jti: str, user_id: str, ttl_seconds: int) -> None: ...

    def get(self, jti: str) -> str | None: ...

    def delete(self, jti: str) -> None: ...


class RedisRefreshTokenStore:
    def __init__(self, client: Redis, prefix: str = "auth:refresh") -> None:
        self._client = client
        self._prefix = prefix

    def _key(self, jti: str) -> str:
        return f"{self._prefix}:{jti}"

    def save(self, jti: str, user_id: str, ttl_seconds: int) -> None:
        self._client.setex(self._key(jti), ttl_seconds, user_id)

    def get(self, jti: str) -> str | None:
        value = self._client.get(self._key(jti))
        return value if isinstance(value, str) else None

    def delete(self, jti: str) -> None:
        self._client.delete(self._key(jti))


def build_token_store() -> RefreshTokenStore:
    settings = get_settings()
    return RedisRefreshTokenStore(get_redis(), settings.refresh_token_prefix)