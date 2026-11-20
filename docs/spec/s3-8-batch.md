# S3-8 批量上传与进度 — 设计（spec）

> 任务卡：S3-8 ｜ 契约：`API.md` **本卡新增** `POST /invoices/batch`、`GET /invoices/batch/{batch_id}`、
> `POST /invoices/batch/{batch_id}/items/{invoice_id}/retry`；`SCHEMA.md` 新增
> `invoices.batch_id` 与 `ocr_jobs.dispatched`（迁移 0006）｜ 上游：S2-6（单文件上传/去重）、
> S2-7（任务框架）、S3-1（流水线）｜ 下游：S5（报销提交按 batch 查看）
> 状态：**已实现（2026-11-20）**；实测：30 文件 POST 1.17s / 轮询 48.6s 全部 succeeded、progress 全 100；
> 批内仅 3 个入队；重试后 invoice 30→30、job 2 条且重跑成功；单元 422 / 集成 237 全绿；迁移 0006 已提交。

## 1. 目标与范围

一次传 30 张：全部建单并入队（服务端限并发 ≤3），前端看总进度与逐项状态，失败项可单条重试且**不重复建单**。

**做**：

| # | 产出 | 说明 |
|---|---|---|
| 1 | `alembic/versions/0006_batch_upload.py`（新） | `invoices.batch_id UUID NULL` + `ix_invoices_batch_id`；`ocr_jobs.dispatched BOOLEAN NOT NULL DEFAULT true` |
| 2 | `models/{invoices,ocr_jobs}.py` + `docs/SCHEMA.md` | 两列与索引 |
| 3 | `core/config.py` + `.env.example` | `OCR_MAX_ACTIVE_PER_USER=3`（单用户并发识别上限） |
| 4 | `services/batch_service.py`（新） | `create_batch` / `dispatch_pending` / `batch_summary` / `retry_item` |
| 5 | `tasks/worker.py` + `services/job_service.py` | `after_job_end` 钩子补位；`create_retry_job` |
| 6 | `api/v1/invoices.py` + `schemas/invoice.py` | 三个 batch 接口与出入参 |
| 7 | `web/src/views/Upload.vue` + `api/{files,invoices}.ts` + `types/invoice.ts` | 批量模式：总进度、逐项状态、失败重试 |
| 8 | 测试 | 迁移/模型、batch 服务与接口、并发补位、重试不重复建单、30 文件验收 |
| 9 | 文档 | `API.md`、`SCHEMA.md`、`PROGRESS`、`DECISIONS D-067`、`HANDOFF`、`AGENTS` |

**不做**：压缩包上传；断点续传；跨用户批量；批量删除/作废；前端虚拟滚动（30 条不需要）。

## 2. 已拍板与偏离（用户 2026-11-20 确认）

| # | 问题 | 裁决 |
|---|---|---|
| Q1 | batch 存储 | **加两列 + 迁移 0006**：`invoices.batch_id UUID`（+索引）、`ocr_jobs.dispatched BOOLEAN DEFAULT true`（区分"已入队/排队中"） |
| Q2 | 并发语义 | **服务端排队 + 自动补位**：批量提交时全部建单，最多 3 个入队；worker `after_job_end` 与 `GET batch` 轮询时补位 |
| Q3 | 失败重试 | **batch 作用域**：`POST /invoices/batch/{batch_id}/items/{invoice_id}/retry`，用原 `invoice_files` 重投，不建新单 |

其余我定的取舍：

| # | 冲突/风险 | 处理 |
|---|---|---|
| C1 | 去重文件（`409/40901`）没有 `invoice` 行，无法按 `batch_id` 聚合 | 去重项只在 `POST /batch` 的**即时响应**里返回（`status=duplicate` + 既有 `invoice_id`）；`GET batch` 只聚合真正建出来的票据；前端把 POST 的 duplicate 项并进列表 |
| C2 | 多文件一次 multipart 上传可能耗时（30×2MB） | 接口只做"校验+写对象+建单+入队"，**不做同步识别**（任务卡坑：Nginx 60s）；30 文件本地实测应 <10s，超时由前端控制单个请求 120s |
| C3 | 服务端限 3 但 worker 全局并发 1（D-057） | 允许：3 是"单用户入队上限"，不是 worker 并发；排队项靠 `after_job_end` 补位，最坏等待可接受 |
| C4 | `batch_id` 用 `uuid4` 生成 | 非密码用途，允许；不用 `secrets`（密码 nonce 才强制） |
| C5 | 重试时旧 job 已 failed/running | 只允许 `failed` 或"无活动 job"；`running/pending(dispatched)` → `409` |
| C6 | 迁移不可回滚？ | 0006 只加列/索引，`downgrade` 可删；遵循"不修改已提交迁移" |

## 3. 设计

### 3.1 迁移 0006

```sql
ALTER TABLE invoices ADD COLUMN batch_id UUID NULL;
CREATE INDEX ix_invoices_batch_id ON invoices (batch_id);
ALTER TABLE ocr_jobs ADD COLUMN dispatched BOOLEAN NOT NULL DEFAULT true;
```

- `batch_id` 不设外键（批次是弱聚合，批次本身不落表）；
- `ocr_jobs.dispatched`：`true` = 已投递 ARQ（含排队/运行），`false` = 服务端排队中；
  历史行默认 `true`（保持原语义）。
- `alembic check` 必须零差异；`downgrade -1` 可回退。

### 3.2 `batch_service.py`

```python
MAX_BATCH_FILES = 30
MAX_ACTIVE_PER_USER = settings.ocr_max_active_per_user  # 默认 3

@dataclass(frozen=True)
class BatchItemResult:
    filename: str
    status: str                  # created | duplicate | rejected
    invoice_id: int | None
    job_id: int | None
    error: str | None

@dataclass(frozen=True)
class BatchCreated:
    batch_id: UUID
    items: list[BatchItemResult]
    created: int
    duplicates: int
    rejected: int

def create_batch(db, user, uploads: list[UploadFile], *, settings, storage, queue) -> BatchCreated:
    # 1) 文件数 1..30，否则 422
    # 2) 逐个调用 upload_service.upload_invoice（复用魔数/大小/去重/落对象/建单）
    #    - DuplicateInvoiceError → status=duplicate，记既有 invoice_id
    #    - 其他 AppError → status=rejected，记 message（不中断整批）
    #    - 成功 → 记 invoice_id/job_id；把 invoice.batch_id 与 job.dispatched=False 落库
    # 3) dispatch_pending(db, user.id, queue) 补位入队
```

`dispatch_pending(db, uploader_id, queue) -> int`：

1. `active = count(ocr_jobs join invoices where invoices.uploader_id=:u and dispatched and status in ('pending','running'))`；
2. `slots = max(0, MAX_ACTIVE_PER_USER - active)`；为 0 直接返回 0；
3. 取 `dispatched=false and status='pending'` 的 job（join invoices 同用户，`order by ocr_jobs.id`）limit slots；
4. 逐条 `job.dispatched=True` + commit，再 `await queue.enqueue_ocr(job.id)`；入队失败走现有
   `job_service.mark_enqueue_failed`（置 failed，不影响已入队的）；
5. 返回本次补位数量。

`batch_summary(db, user, batch_id) -> BatchSummary`：

- 权限：批次里的票据必须对当前用户可见（submitter 仅本人；reviewer 可见全量；admin 404）——
  以 `invoices.batch_id = :id AND uploader_id = :u`（reviewer 不加 uploader 条件）；
- 每项：`invoice_id, filename, ocr_status, extract_status, progress, error`；
  `progress` 复用 `ocr_status_service.compute_progress`（与 ocr-status 一致）；
- `counts`：pending/running/succeeded/failed 按 `invoice.ocr_status` 汇总；`total = len(items)`；
- 批次不存在或不可见 → 404。

`retry_item(db, user, batch_id, invoice_id, queue) -> RetryResult`：

1. 票据必须属于该 batch 且可见（否则 404）；
2. 有活动 job（`status in ('pending','running')`）→ `409`（避免重复识别）；
3. `job = job_service.create_job(db, invoice_id=..., status=pending)`，设 `dispatched=False`；
   `invoice.ocr_status = 'pending'`、`invoice.extract_status` 不变；
4. `dispatch_pending` 补位；返回 `{invoice_id, job_id}`；**不建新 invoice/file**。

### 3.3 接口

```http
POST /api/v1/invoices/batch            # multipart, files: 1..30
→ 202 {"batch_id":"<uuid>","created":28,"duplicates":2,"rejected":0,
       "items":[{"filename":"a.jpg","status":"created","invoice_id":101,"job_id":51,"error":null}, ...]}

GET /api/v1/invoices/batch/{batch_id}
→ 200 {"batch_id","total","counts":{"pending":0,"running":1,"succeeded":28,"failed":1},
       "items":[{"invoice_id","filename","ocr_status","extract_status","progress","error"}], "created_at"}

POST /api/v1/invoices/batch/{batch_id}/items/{invoice_id}/retry
→ 202 {"invoice_id":105,"job_id":60}
```

- 权限：submitter（本人）；reviewer 可读 batch（与票据读策略一致）、不可建批/重试；admin 404；
- 错误：`422/42200` 文件数越界/空批；`404/40400` 批次或票据不属于该批；`409/40900` 有进行中作业；
  `503/50300` 队列不可用（已建单items 保留，可重试）；
- 备注：接口**不做同步识别**；`duplicates` 只出现在 POST 响应（C1）；`GET` 轮询建议 2–3s，完成即停。

### 3.4 worker 补位

`WorkerSettings.after_job_end(ctx, job)`：

```python
async def after_job_end(ctx: dict, job) -> None:
    """一个识别任务结束后，为该用户补位下一个排队作业（单用户并发 ≤3）。"""
    job_id = job.args[0] if job.args else None
    if job_id is None:
        return
    await asyncio.to_thread(batch_service.dispatch_after_job, int(job_id), ctx.get("queue"))
```

`dispatch_after_job(sync)`：查 job→invoice→uploader_id，建 session 调 `dispatch_pending`；
`ctx["queue"]` 由 `on_startup` 注入 `ArqOcrTaskQueue`（或直接复用 worker 的 redis 设置）。

### 3.5 前端批量模式

- 文件 >1 时走 `POST /invoices/batch`（单文件仍走现有串行路径，避免无谓变更）；
- 上传进度：multipart 上传进度（axios `onUploadProgress` 汇总）；
- 上传完成后按 `batch_id` 每 3s 轮询 `GET batch`，总进度 = `(succeeded+failed)/total`；
- 每项显示：文件名、状态标签、progress、错误；`failed` 项显示"重试"按钮 → retry 接口 → 重新轮询；
- 去重项显示"已上传 + 查看"（复用现有 409 绿色样式语义）；
- 完成后若全部成功，给"查看票据列表"入口（不自动跳详情）。

## 4. 测试设计

**迁移/模型（集成）**
- `alembic upgrade head` / `downgrade -1` / `upgrade head`；`alembic check` 零差异；
- `invoices.batch_id` 可空、`ix_invoices_batch_id` 存在；`ocr_jobs.dispatched` 默认 true。

**batch 服务（集成 + 假 storage/queue）**
- 5 个唯一文件 → `created=5`、5 张 invoice、5 个 job；`dispatched=true` 的恰为 **3**，其余 false；
- 同文件重复两次 → `created=1, duplicates=1`，且不重复建单；
- 1 个非法文件（txt）→ `rejected=1`，其他照常创建；
- `dispatch_pending` 补位：手动把 1 个 job 置 succeeded（模拟结束）→ 再调 → 下一个排队 job 变 dispatched=true 且入队；
- `batch_summary`：counts/items/progress 正确；他人 404；reviewer 可见；
- `retry_item`：失败票据 → 新 job（invoice 数不变）；running → 409；跨批 → 404。

**30 文件验收（集成）**
- 30 个小 JPEG（不同字节）→ POST batch `created=30`；GET `total=30`；
- 模拟全部 succeeded（直接改 job/invoice）→ `counts.succeeded=30`、progress 100%；
- 失败 1 个后 retry → invoice 总数仍 30。

**接口（HTTP）**
- 空批/31 个 → 422；403/404 权限；`GET` 404；retry 409/202；入队失败 503 时已建单保留。

**前端**：`typecheck`/`build`；真实冒烟 30 文件（见 Task 4）。

## 5. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic upgrade head
../.venv/Scripts/python.exe -m alembic check           # 零差异（0006）
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
# 冒烟：30 文件批量上传 → 轮询到 100%；失败 1 条重试后票据总数不变
```

## 6. 收口

- `PROGRESS` 勾 S3-8 + 实测（30 文件总耗时、并发 3、补位、重试不重复建单）；
- `DECISIONS`：**D-067**（batch 两列 + 迁移 0006；服务端排队 3 + after_job_end 补位；retry 不建新单；
  duplicates 只在 POST 响应；不做同步识别）；
- `SCHEMA.md` 两表补列；`API.md` §2 加三接口；`AGENTS` §10.8 锚点；`HANDOFF` 下一步 S3-9 /
  阶段 3 收尾；
- 迁移 0006 提交（不修改 0001–0005）；提交 `feat(s3-8): ...`（push 由用户）。