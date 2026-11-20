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
from app.services import batch_service, job_service
from app.tasks.ocr_task import anchor_audit_task, ocr_task, reocr_task
from app.tasks.queue import ArqOcrTaskQueue

logger = logging.getLogger("app.tasks.worker")


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    functions = [ocr_task, reocr_task, anchor_audit_task]
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
        # 批量排队补位需要队列句柄（S3-8）；与 API 共用同一份 Redis 设置
        ctx["queue"] = ArqOcrTaskQueue(_redis_settings())
        # 上一条进程可能留下 running 的僵尸作业；启动时统一收口
        recovered = await asyncio.to_thread(job_service.recover_stale_jobs)
        if recovered:
            logger.warning(
                "worker 启动：回收僵尸作业",
                extra={"event": "stale_jobs_recovered", "task_id": recovered},
            )

    @staticmethod
    async def after_job_end(ctx: dict, job) -> None:
        """一个识别任务结束后，为该用户补位下一个排队作业（单用户并发 ≤N，S3-8）。"""
        if not job.args:
            return
        try:
            moved = await batch_service.dispatch_for_job(
                int(job.args[0]), queue=ctx["queue"], settings=get_settings()
            )
        except Exception:  # noqa: BLE001 - 补位失败不能影响 worker 主循环
            logger.warning("批量补位失败", extra={"event": "batch_dispatch_failed"})
            return
        if moved:
            logger.info(
                "批量补位", extra={"event": "batch_dispatched", "task_id": moved}
            )

    @staticmethod
    async def on_shutdown(ctx: dict) -> None:
        return None