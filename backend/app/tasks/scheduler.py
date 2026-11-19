"""定时任务骨架（S2-12）。

设计要点：
- **每进程都有调度器**（APScheduler 是进程内的），但**每次执行前抢 Redis 锁**，
  多副本/多 worker 同一次任务只会有一个真正执行；
- 锁的释放用 Lua 比对 token：任务跑过 TTL 后，旧进程不能删掉新进程的锁；
- 现在的 `anchor_audit_task` 还是 S2-7 的占位（只日志），S4-7 替换实现，调度骨架不动。
"""
from __future__ import annotations

import logging
import secrets
from typing import Protocol
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from redis import Redis

from app.core.config import Settings, get_settings
from app.core.redis import get_redis
from app.tasks.ocr_task import anchor_audit_task

logger = logging.getLogger("app.scheduler")

ANCHOR_JOB_ID = "anchor_audit"

# 只有 token 仍属于自己才删除，避免 TTL 过期后误删别人的锁
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class SchedulerLock(Protocol):
    def acquire(self, key: str, token: str, ttl_seconds: int) -> bool: ...

    def release(self, key: str, token: str) -> None: ...


class RedisSchedulerLock:
    def __init__(self, client: Redis | None = None) -> None:
        self._client = client or get_redis()

    def acquire(self, key: str, token: str, ttl_seconds: int) -> bool:
        return bool(self._client.set(key, token, nx=True, ex=ttl_seconds))

    def release(self, key: str, token: str) -> None:
        self._client.eval(_RELEASE_SCRIPT, 1, key, token)


async def run_anchor_audit_once(
    *, lock_store: SchedulerLock | None = None, settings: Settings | None = None
) -> bool:
    """抢锁后执行一次锚定任务；拿不到锁说明别的实例在跑，直接跳过。"""
    settings = settings or get_settings()
    lock = lock_store or RedisSchedulerLock()
    token = secrets.token_hex(16)

    if not lock.acquire(settings.anchor_lock_key, token, settings.anchor_lock_ttl_seconds):
        logger.info(
            "锚定任务已被其他实例执行，跳过",
            extra={"event": "anchor_skipped", "error_code": "ANCHOR_LOCKED"},
        )
        return False

    try:
        await anchor_audit_task({})
        logger.info(
            "锚定任务执行完成",
            extra={"event": "anchor_executed", "error_code": "ANCHOR_OK"},
        )
        # 成功后**不释放**：锁保留到 TTL，作为"这个调度时段已经跑过"的去重标记。
        # 如果立刻释放，另一个副本在几毫秒后就会拿到锁，同一 tick 会执行两次
        # （占位任务是瞬时的，实测必现）。TTL 必须小于调度周期，下一时段自然过期。
        return True
    except Exception:
        # 失败时释放，避免把后续手动重试/下一 tick 挡在锁外；失败原因由 APScheduler 记录
        lock.release(settings.anchor_lock_key, token)
        raise


def build_scheduler(settings: Settings | None = None) -> AsyncIOScheduler:
    settings = settings or get_settings()
    timezone = ZoneInfo(settings.scheduler_timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        run_anchor_audit_once,
        CronTrigger(
            hour=settings.anchor_cron_hour,
            minute=settings.anchor_cron_minute,
            second=settings.anchor_cron_second,
            timezone=timezone,
        ),
        id=ANCHOR_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        kwargs={"settings": settings},
    )
    return scheduler


_scheduler: AsyncIOScheduler | None = None


def start_scheduler(settings: Settings | None = None) -> AsyncIOScheduler | None:
    global _scheduler
    settings = settings or get_settings()
    if not settings.scheduler_enabled:
        logger.info("调度器未启用（SCHEDULER_ENABLED=false）", extra={"event": "scheduler_disabled"})
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = build_scheduler(settings)
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "调度器已启动",
        extra={
            "event": "scheduler_started",
            "cron": f"{settings.anchor_cron_hour}:{settings.anchor_cron_minute}:{settings.anchor_cron_second}",
            "timezone": settings.scheduler_timezone,
        },
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler