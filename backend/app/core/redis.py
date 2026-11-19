"""Redis 客户端单例。

S2-2 只用于 refresh token 白名单；S2-7 的 ARQ 队列、S5 的一次性凭据会复用同一个客户端。
健康检查（`core/health.py`）仍用自己的短超时连接：探针不应该被业务连接池的状态影响。
"""
from __future__ import annotations

from functools import lru_cache

from redis import Redis

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.health_timeout_s,
        socket_timeout=settings.health_timeout_s,
    )


def reset_redis() -> None:
    """测试/脚本切换配置后重置单例。"""
    get_redis.cache_clear()