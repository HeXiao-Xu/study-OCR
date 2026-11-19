"""任务队列抽象（S2-7）：生产用 ARQ，测试注入假队列。

为什么不直接在路由里 `create_pool`：入队失败必须能被上传链路捕获并置 job=failed，
抽成协议后测试不需要真实 Redis（AGENTS §10.6：外部依赖注入假实现）。
"""
from __future__ import annotations

import logging
from typing import Protocol

from arq import create_pool
from arq.connections import RedisSettings

from app.core.exceptions import ServiceUnavailableError

logger = logging.getLogger("app.tasks.queue")


class OcrTaskQueue(Protocol):
    async def enqueue_ocr(self, job_id: int) -> None: ...


class ArqOcrTaskQueue:
    def __init__(self, redis_settings: RedisSettings) -> None:
        self._redis_settings = redis_settings

    async def enqueue_ocr(self, job_id: int) -> None:
        try:
            pool = await create_pool(self._redis_settings)
        except Exception as exc:  # noqa: BLE001 - 连接失败统一转 503
            logger.error("任务队列连接失败", extra={"event": "queue_unavailable"})
            raise ServiceUnavailableError("任务队列不可用") from exc
        try:
            await pool.enqueue_job("ocr_task", job_id)
            logger.info(
                "作业已入队",
                extra={"event": "job_enqueued", "error_code": "JOB_ENQUEUED"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("任务入队失败", extra={"event": "queue_enqueue_failed"})
            raise ServiceUnavailableError("任务队列不可用") from exc
        finally:
            await pool.close()