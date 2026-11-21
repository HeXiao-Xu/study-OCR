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


async def reocr_task(ctx: dict, job_id: int) -> dict:
    """S3-3：局部二次识别作业（与 ocr_task 同构，只是 handler 不同）。"""
    attempt = int(ctx.get("job_try", 1) or 1)
    try:
        outcome = await asyncio.to_thread(
            job_service.execute_job_attempt,
            job_id,
            attempt=attempt,
            handler=_run_reocr,
        )
    except job_service.RetryLater as exc:
        raise Retry(defer=exc.delay) from None
    return {"job_id": job_id, "attempt": attempt, "outcome": outcome}


def _run_reocr(db, job) -> None:
    from app.services.reocr_service import run_reocr

    run_reocr(db, job)


def _run_pipeline(db, job) -> None:
    """S3-1：下载 → 渲染/预处理 → 检测识别 → 票种 → 抽取 → 校验 → 落库。

    延迟导入：API 进程不跑流水线，没必要在启动时把 cv2/paddle 拖进来。
    """
    from app.services.ocr_pipeline import run_pipeline

    run_pipeline(db, job)


async def anchor_audit_task(ctx: dict) -> dict:
    """S4-7：每日审计链校验（只读复算，不写审计行、不改链）。

    "锚定"在本项目里的含义就是**每日再验证一次并留下运行日志**：SCHEMA 没有锚定表，
    不做外部时间戳/公证（论文"安全边界"如实写；删掉整条链仍无法发现）。
    """
    from app.core.database import session_scope
    from app.services.audit import verify_chain

    with session_scope() as db:
        result = verify_chain(db)

    if result.ok:
        logger.info(
            "审计链校验通过",
            extra={"event": "audit_chain_ok", "checked_count": result.checked_count},
        )
    else:
        logger.error(
            "审计链校验失败",
            extra={
                "event": "audit_chain_broken",
                "checked_count": result.checked_count,
                "broken_at": result.broken_at,
                "broken_id": result.broken_id,
                "reason": result.reason,
            },
        )
    return {
        "anchored": result.ok,
        "checked_count": result.checked_count,
        "broken_at": result.broken_at,
        "broken_id": result.broken_id,
        "reason": result.reason,
    }