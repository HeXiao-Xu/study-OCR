"""作业（ocr_jobs）状态机与执行（S2-7）。

职责边界：
- **状态机与幂等**在这里；ARQ 只负责投递与重试的宿主（`tasks/`）。
- `ocr_jobs` 是 AGENTS §10.5 明确豁免"handler 不得自行 commit"的表：阶段进度必须中途可见，
  所以 `execute_job_attempt` 每改变一次状态就单独提交（SCHEMA §3.5 已写明）。

状态：`pending → running → succeeded | failed`；可重试失败回到 `pending`。
`retry_count` 记录"已尝试次数"（1 表示第一次尝试），`error` 只存错误码/短消息（≤500 字符）。
`started_at`/`finished_at` 记录本次尝试的真实起止（S3-1，D-056）；写终态前会重读状态指纹，
发现已被别的执行接管就返回 `superseded`，不覆盖别人的状态。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session_factory
from app.core.exceptions import ServiceUnavailableError
from app.core.redis import get_redis
from app.models.enums import OcrStatus
from app.models.ocr_jobs import OcrJob

logger = logging.getLogger("app.jobs")

JOB_LOCK_PREFIX = "job:lock:"
JOB_LOCK_TTL_SECONDS = 600
RETRY_DELAYS = (5, 30, 120)
MAX_TRIES = 3
STALE_RUNNING_SECONDS = 1800
_ERROR_MAX_LEN = 500

Handler = Callable[[Session, OcrJob], None]


class JobLockStore(Protocol):
    def acquire(self, key: str, ttl_seconds: int) -> bool: ...

    def release(self, key: str) -> None: ...


class RedisJobLockStore:
    """`SET key 1 NX EX ttl`：拿不到锁说明同一 job 正在被执行。"""

    def __init__(self, client: Redis) -> None:
        self._client = client

    def acquire(self, key: str, ttl_seconds: int) -> bool:
        return bool(self._client.set(key, "1", nx=True, ex=ttl_seconds))

    def release(self, key: str) -> None:
        self._client.delete(key)


class RetryLater(RuntimeError):
    """可重试失败：由 `tasks/ocr_task.py` 转成 ARQ `Retry(defer=...)`。"""

    def __init__(self, delay: int) -> None:
        self.delay = delay
        super().__init__(f"retry after {delay}s")


class TaskNotReadyError(RuntimeError):
    """流水线尚未接入（S3-1 之前）。永久错误，不重试。"""


class AbortAttempt(RuntimeError):
    """作业已不在 running（被别的执行接管/已终态）：不写状态、不重试，视作 `superseded`。"""


class RetryableTaskError(RuntimeError):
    """业务侧显式标记的可重试错误（网络/临时 IO/内存类）。"""


def is_retryable(exc: BaseException) -> bool:
    """只有网络/临时 IO/内存类错误重试；参数/逻辑错误直接失败。"""
    return isinstance(
        exc,
        (
            RetryableTaskError,
            ServiceUnavailableError,
            ConnectionError,
            TimeoutError,
            OSError,
            MemoryError,
        ),
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def retry_delay(attempt: int) -> int:
    """第 `attempt` 次失败后等待多少秒；表按需扩展，最后一项兜底。"""
    index = max(0, min(attempt - 1, len(RETRY_DELAYS) - 1))
    return RETRY_DELAYS[index]


def job_lock_key(job_id: int) -> str:
    return f"{JOB_LOCK_PREFIX}{job_id}"


def create_job(
    db: Session,
    *,
    invoice_id: int,
    stage: str = "preprocess",
    status: str = OcrStatus.PENDING.value,
) -> OcrJob:
    """只 `add` 不 commit：跟随上传事务一起提交（AGENTS §10.5 的"先落库再入队"）。"""
    job = OcrJob(
        invoice_id=invoice_id,
        stage=stage,
        status=status,
        engine="paddleocr",
        retry_count=0,
    )
    db.add(job)
    return job


def execute_job_attempt(
    job_id: int,
    *,
    attempt: int,
    handler: Handler,
    session_factory=None,
    lock_store: JobLockStore | None = None,
    max_tries: int = MAX_TRIES,
) -> str:
    """执行一次作业尝试，返回 `succeeded/failed/pending/locked/terminal/missing`。

    可重试失败会抛 `RetryLater`（状态已置回 `pending`）；
    其余失败返回 `failed`（不抛，让 ARQ 认为这次投递已处理完）。
    """
    factory = session_factory or get_session_factory()
    locks = lock_store or RedisJobLockStore(get_redis())
    key = job_lock_key(job_id)

    db = factory()
    try:
        job = db.get(OcrJob, job_id)
        if job is None:
            return "missing"
        if job.status in (OcrStatus.SUCCEEDED.value, OcrStatus.FAILED.value):
            # 终态天然幂等：重复投递不再执行
            return "terminal"

        if not locks.acquire(key, JOB_LOCK_TTL_SECONDS):
            return "locked"

        try:
            job.status = OcrStatus.RUNNING.value
            job.retry_count = attempt
            job.error = None
            # 每次尝试都刷新开始时间；回 pending 时清掉上一轮的结束时间（D-056）
            job.started_at = _utcnow()
            job.finished_at = None
            db.commit()
            db.refresh(job)

            started = time.perf_counter()
            try:
                handler(db, job)
            except Exception as exc:  # noqa: BLE001 - 分类后统一落库
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return _persist_failure(
                    db,
                    job_id,
                    exc,
                    attempt=attempt,
                    max_tries=max_tries,
                    elapsed_ms=elapsed_ms,
                )

            # 写终态前重读作业行：ARQ 超时/并发 restore 可能已经把这行改成别的状态，
            # 僵尸线程（to_thread 杀不掉）不能覆盖新一轮的状态（S3-1）
            state = db.execute(
                select(OcrJob.status, OcrJob.retry_count).where(OcrJob.id == job_id)
            ).one()
            if state.status != OcrStatus.RUNNING.value or state.retry_count != attempt:
                db.rollback()          # 撤掉本次尝试的业务写入
                return "superseded"

            job.status = OcrStatus.SUCCEEDED.value
            job.elapsed_ms = int((time.perf_counter() - started) * 1000)
            job.finished_at = _utcnow()
            job.error = None
            db.commit()
            return "succeeded"
        finally:
            locks.release(key)
    finally:
        db.close()


def _persist_failure(
    db: Session,
    job_id: int,
    exc: BaseException,
    *,
    attempt: int,
    max_tries: int,
    elapsed_ms: int,
) -> str:
    # handler 可能已经把会话弄成失败事务；先回滚再写作业状态
    db.rollback()
    job = db.get(OcrJob, job_id)
    if job is None:
        return "missing"

    # 僵尸线程/重复投递：这行已被别的执行接管（状态或尝试次数变了）就不写
    if (
        isinstance(exc, AbortAttempt)
        or job.status != OcrStatus.RUNNING.value
        or job.retry_count != attempt
    ):
        return "superseded"

    message = f"{type(exc).__name__}: {exc}"[:_ERROR_MAX_LEN]
    job.elapsed_ms = elapsed_ms
    job.retry_count = attempt

    if is_retryable(exc) and attempt < max_tries:
        job.status = OcrStatus.PENDING.value
        job.error = message
        job.finished_at = None       # 这一轮没结束，等下一次尝试
        db.commit()
        raise RetryLater(retry_delay(attempt))

    job.status = OcrStatus.FAILED.value
    job.finished_at = _utcnow()
    job.error = (
        f"重试 {max_tries} 次仍失败：{message}" if is_retryable(exc) else message
    )[:_ERROR_MAX_LEN]
    db.commit()
    logger.warning(
        "作业失败",
        extra={
            "event": "job_failed",
            "invoice_id": job.invoice_id,
            "error_code": "JOB_FAILED",
        },
    )
    return "failed"


def recover_stale_jobs(
    *,
    session_factory=None,
    older_than_seconds: int = STALE_RUNNING_SECONDS,
) -> int:
    """worker 启动时把"上一个进程留下的 running"置 failed，避免僵尸态。

    `ocr_jobs` 没有 `started_at`（SCHEMA 契约），用 `created_at` 作为保守近似：
    只有超过 30 分钟仍在 running 的才算中断。
    """
    factory = session_factory or get_session_factory()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    with factory() as db:
        jobs = db.scalars(
            select(OcrJob).where(
                OcrJob.status == OcrStatus.RUNNING.value,
                OcrJob.created_at < cutoff,
            )
        ).all()
        for job in jobs:
            job.status = OcrStatus.FAILED.value
            job.retry_count = max(job.retry_count, 1)
            job.finished_at = _utcnow()
            job.error = "worker 重启或超时，任务中断（S2-7 恢复）"[:_ERROR_MAX_LEN]
        db.commit()
        return len(jobs)


def mark_enqueue_failed(
    job_id: int, *, db: Session | None = None, session_factory=None
) -> None:
    """入队失败：作业置 failed（AGENTS §10.5"入队失败置 failed"）。"""

    def _mark(session: Session) -> None:
        job = session.get(OcrJob, job_id)
        if job is None or job.status != OcrStatus.PENDING.value:
            return
        job.status = OcrStatus.FAILED.value
        job.finished_at = _utcnow()
        job.error = "任务入队失败（队列不可用）"[:_ERROR_MAX_LEN]
        session.commit()

    if db is not None:
        _mark(db)
        return

    factory = session_factory or get_session_factory()
    with factory() as session:
        _mark(session)