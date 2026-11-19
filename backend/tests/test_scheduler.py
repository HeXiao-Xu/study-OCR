"""定时任务骨架测试（S2-12）：锁、cron、开关。全部用假锁，不连真实 Redis。"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.tasks import scheduler as sched


class FakeLock:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.calls: list[tuple[str, str, int]] = []
        self.released: list[tuple[str, str]] = []

    def acquire(self, key: str, token: str, ttl_seconds: int) -> bool:
        self.calls.append((key, token, ttl_seconds))
        return self.acquired

    def release(self, key: str, token: str) -> None:
        self.released.append((key, token))


def _settings(**overrides) -> Settings:
    base = {
        "environment": "test",
        "database_url": "postgresql://u:p@127.0.0.1:5433/db",
        "redis_url": "redis://:p@127.0.0.1:6380/0",
        "minio_endpoint": "127.0.0.1:9002",
        "minio_access_key": "test",
        "minio_secret_key": "test",
        "minio_bucket": "test",
        "jwt_secret": "x" * 32,
        "scheduler_enabled": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


async def test_anchor_skips_when_lock_held(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(sched, "anchor_audit_task", lambda ctx: called.append(ctx))
    lock = FakeLock(acquired=False)

    executed = await sched.run_anchor_audit_once(lock_store=lock, settings=_settings())

    assert executed is False
    assert called == []
    assert lock.released == []


async def test_anchor_executes_and_keeps_lock_for_period(monkeypatch) -> None:
    called = []

    async def fake_task(ctx):
        called.append(ctx)

    monkeypatch.setattr(sched, "anchor_audit_task", fake_task)
    lock = FakeLock(acquired=True)
    settings = _settings()

    executed = await sched.run_anchor_audit_once(lock_store=lock, settings=settings)

    assert executed is True
    assert called == [{}]
    assert lock.calls[0][0] == settings.anchor_lock_key
    assert lock.calls[0][2] == settings.anchor_lock_ttl_seconds
    # 成功后不释放：锁是"本时段已执行"的标记，靠 TTL 过期进入下一时段
    assert lock.released == []


async def test_anchor_releases_lock_even_when_task_fails(monkeypatch) -> None:
    async def boom(ctx):
        raise RuntimeError("anchor failed")

    monkeypatch.setattr(sched, "anchor_audit_task", boom)
    lock = FakeLock(acquired=True)

    with pytest.raises(RuntimeError):
        await sched.run_anchor_audit_once(lock_store=lock, settings=_settings())

    assert lock.released


def test_build_scheduler_registers_daily_job(settings: Settings) -> None:
    scheduler = sched.build_scheduler(settings)
    job = scheduler.get_job(sched.ANCHOR_JOB_ID)
    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
    assert scheduler.timezone == ZoneInfo(settings.scheduler_timezone)
    assert "hour='2'" in str(job.trigger)
    assert "minute='0'" in str(job.trigger)


async def test_start_and_shutdown_scheduler(settings: Settings) -> None:
    sched.shutdown_scheduler()
    scheduler = sched.start_scheduler(settings.model_copy(update={"scheduler_enabled": True}))
    assert scheduler is not None
    assert scheduler.running is True
    assert sched.get_scheduler() is scheduler
    sched.shutdown_scheduler()
    assert sched.get_scheduler() is None


def test_scheduler_disabled_does_not_start(settings: Settings) -> None:
    sched.shutdown_scheduler()
    assert sched.start_scheduler(settings.model_copy(update={"scheduler_enabled": False})) is None
    assert sched.get_scheduler() is None


def test_config_rejects_invalid_timezone_and_cron() -> None:
    with pytest.raises(ValidationError):
        _settings(scheduler_timezone="Mars/Base")
    with pytest.raises(ValidationError):
        _settings(anchor_cron_second="every")


def test_config_accepts_test_cron_fields() -> None:
    settings = _settings(
        anchor_cron_hour="*", anchor_cron_minute="*", anchor_cron_second="*/10"
    )
    assert settings.anchor_cron_second == "*/10"