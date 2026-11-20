"""ARQ Worker 配置（S2-7）。

启动：
    cd backend && ../.venv/Scripts/python.exe -m arq app.tasks.worker.WorkerSettings

与 API 共用同一份 `Settings`（`core/config.py`），不抄第二份 `DATABASE_URL`（AGENTS §10.5）。
"""
from __future__ import annotations

import asyncio
import logging

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.services import job_service
from app.tasks.ocr_task import anchor_audit_task, ocr_task

logger = logging.getLogger("app.tasks.worker")


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    functions = [ocr_task, anchor_audit_task]
    redis_settings = _redis_settings()

    max_tries = job_service.MAX_TRIES
    job_timeout = 180  # 单任务硬超时（AGENTS §10.5）
    # PaddleOCR 模型非线程安全 + CPU 推理吃满核心：默认并发 1（可配 1–2，S3-1/D-057）
    max_jobs = get_settings().ocr_worker_max_jobs
    retry_jobs = True
    keep_result = 3600
    health_check_interval = 30

    @staticmethod
    async def on_startup(ctx: dict) -> None:
        # 上一条进程可能留下 running 的僵尸作业；启动时统一收口
        recovered = await asyncio.to_thread(job_service.recover_stale_jobs)
        if recovered:
            logger.warning(
                "worker 启动：回收僵尸作业",
                extra={"event": "stale_jobs_recovered", "task_id": recovered},
            )

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        return None