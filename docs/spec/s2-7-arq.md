# S2-7 ARQ 异步任务框架 — 设计（spec）

> 任务卡：S2-7 ｜ 契约：`docs/API.md` §2（上传 202 + `job_id`、`ocr-status` 轮询）＋ `SCHEMA.md` §3.5 / §9.9（`ocr_jobs` 列与状态值）｜ 上游：S2-6 已落 `ocr_jobs(pending)` ｜ 下游：S3-1 识别流水线
> 状态：**已实现（2026-11-19）**；§7 的占位行为按方案 A 落地，实测见 §6。

## 1. 目标与范围

让"待识别作业"**可投递、可重试、不重复执行**：ARQ worker、作业状态机、Redis 幂等锁、
可重试/永久错误分类、退避、僵尸 `running` 恢复，并把 S2-6 的上传接到队列上。

**做**：
- `app/tasks/worker.py`：`WorkerSettings`（注册 `ocr_task` + `anchor_audit_task` 占位；`RedisSettings` 从同一份 `Settings` 读；
  `max_tries=3`、`job_timeout=180`、启动时恢复僵尸任务）；
- `app/tasks/ocr_task.py`：`ocr_task`（调 `job_service` + 把内部重试异常映射成 ARQ `Retry`）、
  `anchor_audit_task`（S4-7 占位，记日志返回）；
- `app/tasks/queue.py`：`OcrTaskQueue` 协议 + `ArqOcrTaskQueue`（`enqueue_job("ocr_task", job_id)`）；
- `app/services/job_service.py`：`create_job` / `execute_job_attempt` / `recover_stale_jobs` /
  `mark_enqueue_failed`；作业状态机、重试分类、Redis `SET NX EX 600` 幂等锁；
- `api/v1/files.py`：上传成功后**入队**；入队失败把 job 置 `failed`（AGENTS §10.5）；
- `tests/test_jobs.py`（作业框架）+ 更新 `tests/test_upload.py`（假队列）。

**不做**：真实 OCR 流水线（S3-1）；`anchor_audit_task` 的实际锚定（S4-7）；
`GET /invoices/{id}/ocr-status`（S3-1）；定时任务（APScheduler，S2-12）。

## 2. 契约口径（SCHEMA/API.md 为准）

| 项 | 落地 |
|---|---|
| `ocr_jobs.status` | **`pending / running / succeeded / failed`**（SCHEMA §9.9）；任务卡的 `low_confidence` **不采用**（它属于字段/置信度层，不是作业状态） |
| 尝试次数 | 用 `retry_count`（SCHEMA 列）；任务卡的 `attempts/started_at/finished_at` **不存在**，不偷偷加列 |
| 耗时 | `elapsed_ms`（每次尝试结束写）；`error` 只存错误码+短消息（≤500 字符，**不含明文票据内容**） |
| 入队 | 事务提交后 `enqueue_job`；入队失败 → job `failed` + 记错误，上传返回 503（不返回"假成功"） |
| 幂等 | Redis `SET NX EX 600`，key `job:lock:{job_id}`；重复投递/并发执行只跑一次 |
| 重试 | `max_tries=3`；仅网络/临时 IO/内存类错误重试；参数/逻辑错误直接 `failed` |
| 退避 | 第 1 次失败后 **5s**、第 2 次后 **30s**；第 3 次失败即终态（任务卡写的 120s 预留给后续调大 `max_tries`） |
| 超时 | `job_timeout=180`（AGENTS §10.5） |
| 僵尸恢复 | worker 启动时把 `status='running'` 且 `created_at` 超过 30min 的作业置 `failed`（"worker 重启/超时中断"） |

## 3. 设计

```
tasks/worker.py      WorkerSettings(functions=[ocr_task, anchor_audit_task], max_tries=3,
                     job_timeout=180, retry_jobs=True, redis_settings=from_dsn(settings.redis_url),
                     on_startup=recover_stale_jobs)
tasks/ocr_task.py    async ocr_task(ctx, job_id): attempt=ctx["job_try"]; await to_thread(execute_job_attempt,...)
                     RetryLater → raise arq.worker.Retry(defer=delay)
tasks/queue.py       ArqOcrTaskQueue.enqueue_ocr(job_id)（create_pool → enqueue_job → close）
services/job_service.py
  create_job(db, invoice_id, stage="preprocess")      # 不 commit，跟随上传事务
  execute_job_attempt(job_id, attempt, handler, ...)  # 锁 → running → handler → 终态/重试
  recover_stale_jobs(...) / mark_enqueue_failed(...)
  JobLockStore 协议 + RedisJobLockStore（set nx ex / delete）
  RetryLater(delay) / TaskNotReadyError / is_retryable()
```

**`execute_job_attempt` 流程**（唯一作业执行入口）：
1. 取 job；不存在 → 返回 `missing`；
2. 终态（`succeeded/failed`）→ 返回 `terminal`（天然幂等）；
3. 取 Redis 锁，失败 → 返回 `locked`（重复投递直接跳过，不碰状态）；
4. `pending → running`，`retry_count=attempt`，清空 `error`，提交（`ocr_jobs` 是 AGENTS §10.5 的
   明确豁免：阶段进度必须中途可见，SCHEMA §3.5 已写明）；
5. 调 handler（S3-1 的真实流水线；本卡是 `TaskNotReadyError` 占位）；
6. 成功 → `succeeded` + `elapsed_ms`；
   可重试且 `attempt < max_tries` → `pending` + `error` + 提交，抛 `RetryLater(delay)`；
   可重试且已到上限 → `failed` + "重试 3 次仍失败：…"；
   永久错误 → `failed` + 原因；
7. `finally` 释放锁（重试需要能重新获取）。

**为什么状态机在 service 而不是 worker**：worker 只是投递/重试的宿主；`job_service` 可被
测试、脚本、S3 复用，ARQ 被替换也不动业务状态机。

## 4. 与任务卡/契约的冲突（报告）

1. **列名**：任务卡要求写 `attempts / started_at / finished_at`；SCHEMA 没有这三列。
   本卡用 `retry_count / created_at / elapsed_ms`，**不新增契约外列**。
2. **状态值**：任务卡写 `low_confidence`；SCHEMA §9.9 只有 4 值。`low_confidence` 属于字段置信度
   （`extract_status=need_confirm` / `invoice_fields.need_review`），不进作业状态。
3. **退避**：任务卡 `max_tries=3` 与 `5s/30s/120s` 三个间隔在"三次尝试"下只有两个间隔可用；
   本卡用 5s/30s，120s 留在常量表里给后续调大 `max_tries` 时使用。
4. **依赖**：ARQ 0.28 硬性要求 `redis<6`，装它会把 redis-py 从 **8.1.0 降到 5.3.1**（并引入 hiredis）。
   这是 AGENTS §2 指定 ARQ 的连带结果；已跑全量回归确认认证/健康检查不受影响，记 DECISIONS。

## 5. 测试设计

**单元（默认 `pytest`）**
- `is_retryable`：`OSError/ConnectionError/TimeoutError/MemoryError/RetryableTaskError/ServiceUnavailableError` → True；
  `TaskNotReadyError/ValueError/KeyError` → False；
- 退避表：5/30；
- `WorkerSettings`：函数名含 `ocr_task`/`anchor_audit_task`、`max_tries=3`、`job_timeout=180`；
- `ocr_task` 把 `RetryLater` 映射为 ARQ `Retry(defer=...)`。

**集成（临时库 + 假锁 + 假 handler）**
- 成功：pending → running → succeeded，`retry_count=1`、`elapsed_ms` 非空；
- 重复执行：第二次调用 handler 不再运行（terminal 跳过）；
- 锁占用：返回 `locked`，状态不变、handler 不运行；
- 可重试失败：第 1 次 → `RetryLater(5)` + `pending` + `retry_count=1` + error；
  第 2 次 → `RetryLater(30)`；第 3 次 → `failed` + 终态；
- 永久失败：第 1 次即 `failed`，不重试；
- 僵尸恢复：`running` 且 `created_at` 老于阈值 → `failed`；新的 `running` 不动；
- 上传入队：假队列收到 `job_id`；队列抛错 → 202 变 503 + job `failed`。

## 6. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest -p no:warnings
../.venv/Scripts/python.exe -m pytest -m integration -p no:warnings
../.venv/Scripts/python.exe -m alembic check
# 手工：起 worker（另开一个后台进程）→ 上传 → 观察 ocr_jobs 从 pending 到 failed（S3-1 未接入）
cd backend && ../.venv/Scripts/python.exe -m arq app.tasks.worker.WorkerSettings
```

**实测（2026-11-19）**：`tests/test_jobs.py` **12 单元 + 7 集成**、`tests/test_upload.py` 12 条；
`pytest 323 passed`、`pytest -m integration 137 passed`；真实 ARQ worker + uvicorn 手工：
上传 202 → worker 取到 job → `ocr_jobs(status=failed, retry_count=1, error="TaskNotReadyError: OCR 流水线未接入（S3-1）")`；
smoke 8 OK / 0 FAIL；测试数据与 MinIO 对象已清理。

## 7. 待确认：`ocr_task` 在 S3-1 之前的行为

- **方案 A（推荐）**：`ocr_task` 立刻失败并写清原因（"OCR 流水线未接入（S3-1）"），
  job 状态 `failed`；框架的重试/幂等用测试注入的 handler 验证。**不伪造 succeeded**（AGENTS §7 禁止假数据）。
- 方案 B：让 `ocr_task` 空跑并标 `succeeded`。上传链路看起来"通了"，但 `invoices.ocr_status` 会假成功，
  S3-1 上线前所有人都会以为识别做完了——**不做**。

**已拍板（2026-11-19，方案 A）**：`ocr_task` 在 S3-1 前如实 `failed`；框架能力由测试注入的 handler 验证。

## 8. 收口

- `PROGRESS` 勾 S2-7 + 实测；`DECISIONS` D-045（ARQ + redis 降级；retry_count 语义；僵尸恢复阈值）；
- `AGENTS` §10.8 回填"异步任务框架"锚点；
- `HANDOFF` 下一步 S2-8；
- 提交 `feat(s2-7): ...`；**push 由用户执行**。