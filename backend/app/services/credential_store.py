"""一次性凭据存储（S5-4）：`verify_token` 与 `preview_token` 的 Redis 实现 + 测试用内存实现。

为什么单独一层：验签与预览都是"30min 有效的凭据"，S5-5 的审核还要**消费** verify_token；
接口做成协议后，测试可以注入内存实现，不依赖 Redis 服务（与 `token_store.py` 同一模式）。
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Protocol

from redis import Redis

from app.core.redis import get_redis

__all__ = [
    "CredentialStore",
    "InMemoryCredentialStore",
    "RedisCredentialStore",
    "get_credential_store",
    "verify_key",
    "preview_key",
]

_PREFIX = "finance"


def verify_key(report_id: int) -> str:
    return f"{_PREFIX}:verify:{report_id}"


def preview_key(token: str) -> str:
    return f"{_PREFIX}:preview:{token}"


class CredentialStore(Protocol):
    def put(self, key: str, value: str, ttl_seconds: int) -> None: ...

    def get(self, key: str) -> str | None: ...

    def delete(self, key: str) -> None: ...


class RedisCredentialStore:
    def __init__(self, client: Redis) -> None:
        self._client = client

    def put(self, key: str, value: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            # TTL 为 0 视为"立刻过期"，不写（SETEX 不接受 0）
            return
        self._client.setex(key, ttl_seconds, value)

    def get(self, key: str) -> str | None:
        value = self._client.get(key)
        return value if isinstance(value, str) else None

    def delete(self, key: str) -> None:
        self._client.delete(key)


class InMemoryCredentialStore:
    """测试用：可指定 TTL 为 0 模拟"刚过期"。

    时间统一用 `time.monotonic()`，避免测试受系统时钟调整影响。
    """

    def __init__(self) -> None:
        self._items: dict[str, tuple[str, float | None]] = {}

    def put(self, key: str, value: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            self._items.pop(key, None)
            return
        self._items[key] = (value, time.monotonic() + ttl_seconds)

    def get(self, key: str) -> str | None:
        item = self._items.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and expires_at <= time.monotonic():
            self._items.pop(key, None)
            return None
        return value

    def delete(self, key: str) -> None:
        self._items.pop(key, None)


@lru_cache(maxsize=1)
def get_credential_store() -> CredentialStore:
    return RedisCredentialStore(get_redis())