"""ARQ 任务函数（S2-7）。

`ocr_task` 目前把真实流水线委托给 `_run_pipeline`，而它在 S3-1 之前会**如实失败**：
不伪造 `succeeded`（AGENTS §7）。框架的重试/幂等由 `job_service.execute_job_attempt` 保证，
S3-1 只需要替换 `_run_pipeline` 的实现。
"""
from __future__ import annotations

import asyncio
import logging

from arq.worker import Retry

from app.services import job_service

logger = logging.getLogger("app.tasks.ocr")


async def ocr_task(ctx: dict, job_id: int) -> dict:
    attempt = int(ctx.get("job_try", 1) or 1)
    try:
        outcome = await asyncio.to_thread(
            job_service.execute_job_attempt,
            job_id,
            attempt=attempt,
            handler=_run_pipeline,
        )
    except job_service.RetryLater as exc:
        # ARQ 的 Retry 会按 defer 重新投递同一个 job
        raise Retry(defer=exc.delay) from None
    return {"job_id": job_id, "attempt": attempt, "outcome": outcome}


def _run_pipeline(db, job) -> None:
    """S3-1 接入：intake → preprocess → detect/recognize → layout → extract → validators。"""
    raise job_service.TaskNotReadyError("OCR 流水线未接入（S3-1）")


async def anchor_audit_task(ctx: dict) -> dict:
    """S4-7 的每日审计链锚定占位：现在只返回未实现，不写任何假审计。"""
    logger.info("审计锚定任务尚未实现（S4-7）", extra={"event": "audit_anchor_skipped"})
    return {"anchored": False, "reason": "S4-7 未实现"}