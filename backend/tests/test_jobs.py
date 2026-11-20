"""作业框架测试（S2-7）：状态机、幂等锁、重试分类/退避、僵尸恢复、ARQ worker 配置。

集成部分用临时库 + 假锁 + 假 handler，不依赖真实 Redis/worker。
"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from arq.worker import Retry
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.services import job_service
from app.tasks.ocr_task import ocr_task
from tests.db_helpers import alembic_config, temporary_database


class FakeLockStore:
    def __init__(self) -> None:
        self.locked: set[str] = set()

    def acquire(self, key: str, ttl_seconds: int) -> bool:
        if key in self.locked:
            return False
        self.locked.add(key)
        return True

    def release(self, key: str) -> None:
        self.locked.discard(key)


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def job_database() -> Iterator[URL]:
    with temporary_database("study_ocr_job_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(job_database: URL):
    eng = create_engine(
        job_database.render_as_string(hide_password=False), poolclass=NullPool
    )
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def clean_db(engine):
    with engine.begin() as conn:
        conn.execute(sa_text("TRUNCATE users RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def owner(session_factory, clean_db) -> User:
    with session_factory() as db:
        user = User(username="alice", real_name="A", role="submitter", status="active")
        db.add(user)
        db.commit()
        return user


def _create_job(session_factory, uploader_id: int) -> int:
    with session_factory() as db:
        file = InvoiceFile(
            uploader_id=uploader_id,
            filename="a.jpg",
            mime="image/jpeg",
            size=1024,
            sha256=secrets.token_hex(32),
            storage_key=f"invoices/{secrets.token_hex(8)}.bin",
        )
        db.add(file)
        db.flush()
        invoice = Invoice(uploader_id=uploader_id, file_id=file.id)
        db.add(invoice)
        db.flush()
        job = job_service.create_job(db, invoice_id=invoice.id)
        db.commit()
        return job.id


def _get_job(session_factory, job_id: int) -> OcrJob:
    with session_factory() as db:
        return db.get(OcrJob, job_id)


# --------------------------------------------------------------------------- #
# 单元
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (job_service.RetryableTaskError("net"), True),
        (ConnectionError("reset"), True),
        (TimeoutError("slow"), True),
        (OSError("io"), True),
        (MemoryError(), True),
        (job_service.TaskNotReadyError("not ready"), False),
        (ValueError("bad"), False),
        (KeyError("missing"), False),
    ],
)
def test_is_retryable(exc: BaseException, expected: bool) -> None:
    assert job_service.is_retryable(exc) is expected


def test_retry_delay_schedule() -> None:
    assert [job_service.retry_delay(attempt) for attempt in (1, 2, 3)] == [5, 30, 120]


def test_worker_settings_registers_tasks() -> None:
    from app.tasks.worker import WorkerSettings

    names = [
        getattr(function, "name", None) or function.__name__
        for function in WorkerSettings.functions
    ]
    assert "ocr_task" in names
    assert "anchor_audit_task" in names
    assert WorkerSettings.max_tries == 3
    assert WorkerSettings.job_timeout == 180


async def test_ocr_task_maps_retrylater_to_arq_retry(monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise job_service.RetryLater(5)

    monkeypatch.setattr(job_service, "execute_job_attempt", _raise)
    with pytest.raises(Retry) as excinfo:
        await ocr_task({"job_try": 1}, 1)
    assert excinfo.value.defer_score == 5000


async def test_ocr_task_returns_outcome(monkeypatch) -> None:
    monkeypatch.setattr(job_service, "execute_job_attempt", lambda *a, **k: "succeeded")
    result = await ocr_task({"job_try": 2}, 7)
    assert result == {"job_id": 7, "attempt": 2, "outcome": "succeeded"}


# --------------------------------------------------------------------------- #
# 集成：状态机 / 锁 / 重试 / 恢复
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_success_marks_job_succeeded(session_factory, owner) -> None:
    job_id = _create_job(session_factory, owner.id)
    calls: list[int] = []

    def handler(db: Session, job) -> None:
        calls.append(job.id)

    outcome = job_service.execute_job_attempt(
        job_id,
        attempt=1,
        handler=handler,
        session_factory=session_factory,
        lock_store=FakeLockStore(),
    )

    assert outcome == "succeeded"
    assert calls == [job_id]
    job = _get_job(session_factory, job_id)
    assert (job.status, job.retry_count) == ("succeeded", 1)
    assert job.elapsed_ms is not None


@pytest.mark.integration
def test_terminal_job_is_skipped(session_factory, owner) -> None:
    job_id = _create_job(session_factory, owner.id)
    calls: list[int] = []

    def handler(db: Session, job) -> None:
        calls.append(job.id)

    locks = FakeLockStore()
    assert job_service.execute_job_attempt(
        job_id, attempt=1, handler=handler, session_factory=session_factory, lock_store=locks
    ) == "succeeded"
    assert job_service.execute_job_attempt(
        job_id, attempt=2, handler=handler, session_factory=session_factory, lock_store=locks
    ) == "terminal"
    assert calls == [job_id]


@pytest.mark.integration
def test_locked_job_is_skipped(session_factory, owner) -> None:
    job_id = _create_job(session_factory, owner.id)
    locks = FakeLockStore()
    locks.locked.add(job_service.job_lock_key(job_id))
    calls: list[int] = []

    outcome = job_service.execute_job_attempt(
        job_id,
        attempt=1,
        handler=lambda db, job: calls.append(job.id),
        session_factory=session_factory,
        lock_store=locks,
    )

    assert outcome == "locked"
    assert calls == []
    assert _get_job(session_factory, job_id).status == "pending"


@pytest.mark.integration
def test_retryable_failure_backs_off_then_fails(session_factory, owner) -> None:
    job_id = _create_job(session_factory, owner.id)
    locks = FakeLockStore()

    def handler(db: Session, job) -> None:
        raise job_service.RetryableTaskError("网络抖动")

    with pytest.raises(job_service.RetryLater) as first:
        job_service.execute_job_attempt(
            job_id, attempt=1, handler=handler, session_factory=session_factory, lock_store=locks
        )
    assert first.value.delay == 5
    job = _get_job(session_factory, job_id)
    assert (job.status, job.retry_count) == ("pending", 1)
    assert job.error is not None

    with pytest.raises(job_service.RetryLater) as second:
        job_service.execute_job_attempt(
            job_id, attempt=2, handler=handler, session_factory=session_factory, lock_store=locks
        )
    assert second.value.delay == 30

    assert job_service.execute_job_attempt(
        job_id, attempt=3, handler=handler, session_factory=session_factory, lock_store=locks
    ) == "failed"
    job = _get_job(session_factory, job_id)
    assert (job.status, job.retry_count) == ("failed", 3)
    assert "重试 3 次仍失败" in job.error


@pytest.mark.integration
def test_permanent_failure_does_not_retry(session_factory, owner) -> None:
    job_id = _create_job(session_factory, owner.id)

    def handler(db: Session, job) -> None:
        raise job_service.TaskNotReadyError("OCR 流水线未接入（S3-1）")

    outcome = job_service.execute_job_attempt(
        job_id,
        attempt=1,
        handler=handler,
        session_factory=session_factory,
        lock_store=FakeLockStore(),
    )

    assert outcome == "failed"
    job = _get_job(session_factory, job_id)
    assert (job.status, job.retry_count) == ("failed", 1)
    assert "S3-1" in job.error


@pytest.mark.integration
def test_recover_stale_running_jobs(session_factory, owner) -> None:
    stale = _create_job(session_factory, owner.id)
    fresh = _create_job(session_factory, owner.id)
    with session_factory() as db:
        db.execute(
            sa_text("update ocr_jobs set status='running', created_at = now() - interval '2 hours' where id = :id"),
            {"id": stale},
        )
        db.execute(
            sa_text("update ocr_jobs set status='running' where id = :id"), {"id": fresh}
        )
        db.commit()

    recovered = job_service.recover_stale_jobs(session_factory=session_factory)

    assert recovered == 1
    assert _get_job(session_factory, stale).status == "failed"
    assert _get_job(session_factory, fresh).status == "running"


@pytest.mark.integration
def test_mark_enqueue_failed(session_factory, owner) -> None:
    job_id = _create_job(session_factory, owner.id)
    job_service.mark_enqueue_failed(job_id, session_factory=session_factory)
    job = _get_job(session_factory, job_id)
    assert job.status == "failed"
    assert "入队失败" in job.error


# --------------------------------------------------------------------------- #
# S3-1：起止时间与终态守卫
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_execute_job_attempt_records_started_and_finished(session_factory, owner) -> None:
    job_id = _create_job(session_factory, owner.id)

    outcome = job_service.execute_job_attempt(
        job_id,
        attempt=1,
        handler=lambda db, job: None,
        session_factory=session_factory,
        lock_store=FakeLockStore(),
    )

    assert outcome == "succeeded"
    job = _get_job(session_factory, job_id)
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.finished_at >= job.started_at


@pytest.mark.integration
def test_execute_job_attempt_retry_clears_finished_at(session_factory, owner) -> None:
    job_id = _create_job(session_factory, owner.id)

    def handler(db: Session, job) -> None:
        raise OSError("磁盘临时错误")

    with pytest.raises(job_service.RetryLater):
        job_service.execute_job_attempt(
            job_id,
            attempt=1,
            handler=handler,
            session_factory=session_factory,
            lock_store=FakeLockStore(),
        )

    job = _get_job(session_factory, job_id)
    assert job.status == "pending"
    assert job.started_at is not None
    assert job.finished_at is None          # 回到 pending 时不写结束时间


@pytest.mark.integration
def test_execute_job_attempt_superseded_does_not_commit(session_factory, owner) -> None:
    """handler 写完业务列后才发现作业已被别的执行改掉 → 业务写入回滚、状态不被覆盖。"""
    job_id = _create_job(session_factory, owner.id)
    with session_factory() as db:
        invoice_id = db.scalar(
            sa_text("select invoice_id from ocr_jobs where id = :id"), {"id": job_id}
        )

    def handler(db: Session, job) -> None:
        db.execute(
            sa_text("update invoices set ocr_status = 'running' where id = :id"),
            {"id": invoice_id},
        )
        # 模拟"另一个执行/僵尸恢复在另一个连接里把这行改成 failed"
        with session_factory() as other:
            other.execute(
                sa_text("update ocr_jobs set status = 'failed', retry_count = 9 where id = :id"),
                {"id": job_id},
            )
            other.commit()

    outcome = job_service.execute_job_attempt(
        job_id,
        attempt=1,
        handler=handler,
        session_factory=session_factory,
        lock_store=FakeLockStore(),
    )

    assert outcome == "superseded"
    with session_factory() as db:
        assert db.get(Invoice, invoice_id).ocr_status == "pending"     # 业务写入回滚
        job = db.get(OcrJob, job_id)
        assert (job.status, job.retry_count) == ("failed", 9)          # 别人的状态没被覆盖