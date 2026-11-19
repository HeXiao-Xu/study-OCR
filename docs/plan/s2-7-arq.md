# S2-7 实现计划（plan）

> 对应 spec：`docs/spec/s2-7-arq.md` ｜ 契约：`docs/API.md` §2 + `SCHEMA.md` §3.5 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 1 — 依赖与队列封装

```bash
.venv/Scripts/python.exe -m pip install "arq==0.28.0"
```

- `requirements.txt`：`arq==0.28.0`；**redis 从 8.1.0 降到 5.3.1**（ARQ 要求 `redis<6`），
  同步 `hiredis==3.5.0`；重新生成锁文件并核对 diff；
- `app/tasks/queue.py`：`OcrTaskQueue` 协议 + `ArqOcrTaskQueue`（`create_pool(RedisSettings.from_dsn(...))` → `enqueue_job("ocr_task", job_id)` → close）；
- `api/deps.py`：`get_task_queue()`（`lru_cache`）。

**验证**：`python -c "import arq, redis; print(arq.__version__, redis.__version__)"`；`pytest -q` 不回归。

## Step 2 — `services/job_service.py`

- `create_job(db, invoice_id, stage="preprocess")`（不 commit，供上传事务使用）；
- `JobLockStore` 协议 + `RedisJobLockStore`（`set(key, nx=True, ex=600)` / `delete`）+ 内存假实现只放测试；
- `execute_job_attempt(job_id, *, attempt, handler, session_factory=None, lock_store=None)`：
  终态跳过 → 取锁 → `running` → handler → `succeeded|pending|failed`（spec §3 的 7 步）；
- `RetryLater(delay)`、`TaskNotReadyError`、`is_retryable`、`retry_delay(attempt)`；
- `recover_stale_jobs(older_than_seconds=1800)`、`mark_enqueue_failed(job_id)`。

**验证**：`pytest -m integration -q tests/test_jobs.py`。

## Step 3 — 任务与 worker

- `tasks/ocr_task.py`：`ocr_task(ctx, job_id)`（`ctx["job_try"]` → attempt；`to_thread` 执行；
  `RetryLater` → `arq.worker.Retry(defer=...)`）；`anchor_audit_task(ctx)` 占位；
- `tasks/worker.py`：`WorkerSettings`（functions / redis_settings / max_tries=3 / job_timeout=180 /
  retry_jobs=True / on_startup 调 `recover_stale_jobs`）；
- `_run_pipeline` 抛 `TaskNotReadyError("OCR 流水线未接入（S3-1）")`（方案 A）。

**验证**：`python -c "from app.tasks.worker import WorkerSettings; print(WorkerSettings.max_tries)"`；
单元测试断言函数注册。

## Step 4 — 上传接队列

- `api/v1/files.py` 改 `async def`：`await asyncio.to_thread(upload_service.upload_invoice, ...)` →
  `await queue.enqueue_ocr(job_id)`；入队异常 → `job_service.mark_enqueue_failed` + 503；
- `tests/test_upload.py` 夹具注入假队列；新增"入队成功/入队失败"用例。

**验证**：`pytest -m integration -q tests/test_upload.py tests/test_jobs.py`。

## Step 5 — 回归与手工

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m pytest -m integration
../.venv/Scripts/python.exe -m alembic check
```

- 手工：后台起 `arq app.tasks.worker.WorkerSettings`，上传一张图，观察 `ocr_jobs` 由 `pending`
  变 `failed`（错误为"OCR 流水线未接入（S3-1）"），说明入队与状态机真的在跑；
  清理测试数据与 MinIO 对象。
- 文档：`DECISIONS` D-045；`PROGRESS`、`HANDOFF`、`AGENTS` 锚点；提交 `feat(s2-7): ...`；不 push。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| ARQ/redis 降级导致既有代码不兼容 | `redis-py 5.3` API 与用法兼容；全量回归把关；不行则回退 8.1 并评估自建队列 |
| 入队成功但 worker 没起 | job 停在 `pending`；启动 worker 后按队列执行；`ocr-status` 可轮询 |
| worker 被 kill | 重启时 `recover_stale_jobs` 把 30min 前的 `running` 置 `failed`；ARQ 也会重投未确认作业 |
| 重复投递 | 终态跳过 + Redis 锁双保险；测试钉住 |