"""ARQ 任务函数（S2-7 / S3-1）。

`ocr_task` 把真实流水线委托给 `services/ocr_pipeline.run_pipeline`；
重试/幂等/终态守卫由 `job_service.execute_job_attempt` 负责，这里只做"ARQ 适配"。
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
    """S3-1：下载 → 渲染/预处理 → 检测识别 → 票种 → 抽取 → 校验 → 落库。

    延迟导入：API 进程不跑流水线，没必要在启动时把 cv2/paddle 拖进来。
    """
    from app.services.ocr_pipeline import run_pipeline

    run_pipeline(db, job)


async def anchor_audit_task(ctx: dict) -> dict:
    """S4-7 的每日审计链锚定占位：现在只返回未实现，不写任何假审计。"""
    logger.info("审计锚定任务尚未实现（S4-7）", extra={"event": "audit_anchor_skipped"})
    return {"anchored": False, "reason": "S4-7 未实现"}