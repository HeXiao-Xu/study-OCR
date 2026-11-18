"""依赖服务健康检查（/health 的实现）。

三条纪律：
1. **单项超时 2s**：任一依赖挂死不能把 /health 拖成几分钟——探针本身超时就成了新故障；
2. **不泄露凭据**：错误信息只回"类型 + 截断后的原因"，绝不回 DSN / 密钥；
3. **可注入**：`run_checks(settings, checkers=...)` 允许测试替换成假实现，不必真连服务。

连接信息全部来自 Settings，不接受调用方传参（避免把密码写进签名）。
"""
from __future__ import annotations

import logging
from typing import Callable

from app.core.config import Settings

logger = logging.getLogger(__name__)

# 检查函数签名：(settings) -> (是否健康, 人类可读的原因)
Checker = Callable[[Settings], tuple[bool, str]]

_MAX_DETAIL = 120


def _short(text: object) -> str:
    """错误信息截断；顺手把可能出现的 DSN 口令段打掉。"""
    raw = str(text).replace("\n", " ")
    for marker in ("password=", "PASSWORD="):
        if marker in raw:
            head, _, tail = raw.partition(marker)
            raw = head + marker + "***" + tail.split(" ", 1)[-1]
    return raw[:_MAX_DETAIL]


def check_database(settings: Settings) -> tuple[bool, str]:
    """执行 `select 1`。用 psycopg 直连（S0-5 还没有 ORM，S2-1 起才引入 SQLAlchemy）。"""
    import psycopg

    timeout = max(1, int(settings.health_timeout_s))
    try:
        with psycopg.connect(settings.database_url, connect_timeout=timeout) as conn:
            with conn.cursor() as cur:
                cur.execute("select 1")
                value = cur.fetchone()
        if value and value[0] == 1:
            return True, "ok"
        return False, "select 1 返回值异常"
    except Exception as exc:  # noqa: BLE001 - 探针要吃掉所有异常并转成状态
        logger.warning("健康检查失败：database", extra={"error_code": "HEALTH_DB"})
        return False, _short(exc)


def check_redis(settings: Settings) -> tuple[bool, str]:
    import redis

    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.health_timeout_s,
            socket_timeout=settings.health_timeout_s,
        )
        try:
            return (True, "ok") if client.ping() else (False, "PING 未返回 PONG")
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("健康检查失败：redis", extra={"error_code": "HEALTH_REDIS"})
        return False, _short(exc)


def check_minio(settings: Settings) -> tuple[bool, str]:
    """确认 bucket 可用（存在且凭据有效）。"""
    import urllib3
    from urllib3.util import Timeout
    from minio import Minio

    try:
        http_client = urllib3.PoolManager(
            timeout=Timeout(connect=settings.health_timeout_s, read=settings.health_timeout_s),
            retries=False,
        )
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            http_client=http_client,
        )
        if client.bucket_exists(settings.minio_bucket):
            return True, "ok"
        return False, f"bucket 不存在：{settings.minio_bucket}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("健康检查失败：minio", extra={"error_code": "HEALTH_MINIO"})
        return False, _short(exc)


DEFAULT_CHECKERS: dict[str, Checker] = {
    "db": check_database,
    "redis": check_redis,
    "minio": check_minio,
}


def run_checks(settings: Settings, checkers: dict[str, Checker] | None = None) -> dict[str, dict]:
    """依次执行检查，返回 {name: {ok, detail}}。某个检查抛异常也只影响自己。"""
    results: dict[str, dict] = {}
    for name, checker in (checkers or DEFAULT_CHECKERS).items():
        try:
            healthy, detail = checker(settings)
        except Exception as exc:  # noqa: BLE001 - 检查器自身崩了也要兜住
            healthy, detail = False, _short(exc)
        results[name] = {"ok": healthy, "detail": detail}
    return results