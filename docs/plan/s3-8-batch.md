# S3-8 批量上传与进度实现计划（plan）

> 对应 spec：[`docs/spec/s3-8-batch.md`](../spec/s3-8-batch.md)（已确认）｜
> 契约：本卡新增 3 个 batch 接口 + `SCHEMA` 两列（迁移 0006）｜ 状态：**已实现（2026-11-20）**，Task 1–5 全部执行并验证
> 执行方式：Task 1→5 顺序；每个 Task 跑完验证命令再继续。

**目标**：`POST /invoices/batch`（1–30 文件）全部建单入队，单用户并发 ≤3、其余服务端排队并由
`after_job_end`/轮询补位；`GET /batch/{id}` 汇总 + 逐项 progress；失败项 scoped retry 用原文件**不建新单**；
前端 Upload.vue 批量模式。

## Global Constraints

- 命令用 `.venv/Scripts/python.exe`；默认后端目录 `E:\BiSheXZY\study-OCR\backend`。
- **只能新增迁移 0006**，不改 0001–0005；`alembic check` 零差异。
- 接口**不做同步识别**（Nginx 60s 坑）；单用户并发上限默认 3（`OCR_MAX_ACTIVE_PER_USER`）。
- 重试**绝不建新 invoice/file**；去重项只在 POST 响应出现。
- 提交 Conventional Commits（`feat(s3-8): ...`）；**push 由用户执行**。

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/alembic/versions/0006_batch_upload.py`（新） | `invoices.batch_id` + 索引；`ocr_jobs.dispatched` |
| `backend/app/models/{invoices,ocr_jobs}.py`、`docs/SCHEMA.md` | 两列模型与契约 |
| `backend/app/core/config.py`、`.env.example` | `ocr_max_active_per_user=3` |
| `backend/app/services/batch_service.py`（新） | `create_batch`/`dispatch_pending`/`dispatch_for_job`/`batch_summary`/`retry_item` |
| `backend/app/tasks/worker.py` | `after_job_end` 补位 |
| `backend/app/api/v1/invoices.py`、`schemas/invoice.py` | 三接口 + 出入参 |
| `web/src/{api/files.ts,api/invoices.ts,types/invoice.ts,views/Upload.vue}` | 批量模式 |
| `backend/tests/{test_batch,test_migration}.py` | 服务/接口/迁移 |
| `docs/API.md`、`PROGRESS`、`DECISIONS`、`HANDOFF`、`AGENTS` | 收口 |

---

### Task 1: 迁移 0006 + 模型 + 配置

**Files:**
- Create: `backend/alembic/versions/0006_batch_upload.py`
- Modify: `backend/app/models/invoices.py`、`backend/app/models/ocr_jobs.py`、`backend/app/core/config.py`、`.env.example`、`docs/SCHEMA.md`
- Test: `backend/tests/test_migration.py`（补）

- [ ] **Step 1: 模型**

```python
# invoices.py
import uuid
from sqlalchemy import Uuid
...
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
...
        Index("ix_invoices_batch_id", "batch_id"),
```

```python
# ocr_jobs.py（Boolean 已可用）
    dispatched: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
```

- [ ] **Step 2: 迁移**

```python
"""batch upload（S3-8）：invoices.batch_id + ocr_jobs.dispatched。"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("batch_id", sa.Uuid(), nullable=True))
    op.create_index("ix_invoices_batch_id", "invoices", ["batch_id"])
    op.add_column(
        "ocr_jobs",
        sa.Column("dispatched", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("ocr_jobs", "dispatched")
    op.drop_index("ix_invoices_batch_id", table_name="invoices")
    op.drop_column("invoices", "batch_id")
```

- [ ] **Step 3: 配置**

```python
    ocr_max_active_per_user: int = Field(default=3, ge=1, le=10)
```

`.env.example`：`OCR_MAX_ACTIVE_PER_USER=3`

- [ ] **Step 4: SCHEMA**

`invoices` 增加 `batch_id uuid NULL`（索引 `ix_invoices_batch_id`，批次弱聚合不建外键）；
`ocr_jobs` 增加 `dispatched boolean NOT NULL DEFAULT true`（false = 服务端排队中）。

- [ ] **Step 5: 验证**

```bash
../.venv/Scripts/python.exe -m alembic upgrade head
../.venv/Scripts/python.exe -m alembic downgrade -1
../.venv/Scripts/python.exe -m alembic upgrade head
../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q tests/test_models.py tests/test_migration.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/alembic/versions/0006_batch_upload.py backend/app/models/invoices.py \
  backend/app/models/ocr_jobs.py backend/app/core/config.py .env.example docs/SCHEMA.md
git commit -m "feat(s3-8): 迁移 0006（invoices.batch_id / ocr_jobs.dispatched）与并发配置"
```

---

### Task 2: `batch_service.create_batch` + `dispatch_pending`

**Files:**
- Create: `backend/app/services/batch_service.py`
- Modify: `backend/app/services/job_service.py`（如缺 `create_job` 已有，直接用）
- Test: `backend/tests/test_batch.py`（新，先服务层）

- [ ] **Step 1: 写失败测试**

```python
"""S3-8 批量：建单/去重/并发 3/补位/汇总/重试。"""
from __future__ import annotations

import io
import secrets
from decimal import Decimal

import pytest
from PIL import Image

from app.core.config import Settings
from app.models.ocr_jobs import OcrJob
from app.models.invoices import Invoice
from app.services import batch_service

# 夹具：临时库 + session_factory + alice（submitter）；FakeStorage/FakeQueue 见 test_upload.py


def _jpeg(tag: int = 0) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40 + tag, 30), (240, 240, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, int]] = []

    async def enqueue_ocr(self, job_id: int) -> None:
        self.enqueued.append(("ocr", job_id))

    async def enqueue_reocr(self, job_id: int) -> None:
        self.enqueued.append(("reocr", job_id))


async def _create(db, user, files, queue):
    created = batch_service.create_batch(
        db, user, files, settings=_settings(), storage=_storage()
    )
    await batch_service.dispatch_pending(
        db, user.id, queue=queue, settings=_settings()
    )
    return created


def _upload(name: str, payload: bytes):
    from starlette.datastructures import Headers, UploadFile
    return UploadFile(file=io.BytesIO(payload), filename=name,
                      headers=Headers({"content-type": "image/jpeg"}))


def test_create_batch_limits_dispatch_to_three(session_factory, alice) -> None:
    queue = FakeQueue()
    files = [_upload(f"{i}.jpg", _jpeg(i)) for i in range(5)]
    with session_factory() as db:
        created = asyncio.run(_create(db, alice, files, queue))

    assert created.created == 5
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Invoice).where(
            Invoice.batch_id == created.batch_id)) == 5
        dispatched = db.scalars(select(OcrJob).join(Invoice).where(
            Invoice.batch_id == created.batch_id, OcrJob.dispatched.is_(True))).all()
        assert len(dispatched) == 3


def test_create_batch_dedup_and_reject_do_not_abort(session_factory, alice) -> None:
    same = _jpeg(1)
    files = [_upload("a.jpg", same), _upload("b.jpg", same), _upload("c.txt", b"not-image")]
    created = batch_service.create_batch(...)
    assert created.created == 1
    assert created.duplicates == 1
    assert created.rejected == 1


def test_dispatch_pending_fills_slot_after_completion(session_factory, alice) -> None:
    queue = FakeQueue()
    files = [_upload(f"{i}.jpg", _jpeg(i)) for i in range(5)]
    with session_factory() as db:
        created = asyncio.run(_create(db, alice, files, queue))
    with session_factory() as db:
        first = db.scalar(select(OcrJob).join(Invoice).where(
            Invoice.batch_id == created.batch_id, OcrJob.dispatched.is_(True)).order_by(OcrJob.id))
        first.status = "succeeded"
        db.commit()
    moved = await_ = asyncio.run(batch_service.dispatch_pending(
        db=db, uploader_id=alice.id, queue=queue))
    assert moved == 1
```

（`_settings/_storage` 夹具照 `tests/test_upload.py`；异步调用用 `asyncio.run`，测试同步。）

- [ ] **Step 2: 实现**

```python
"""批量上传与排队（S3-8）：建单复用单文件上传；单用户并发 ≤N，其余排队由 after_job_end 补位。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import AppError, DuplicateInvoiceError, NotFoundError, ValidationFailedError
from app.models.enums import OcrStatus
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.services import job_service, ocr_status_service, upload_service
from app.services.storage import StorageService

MAX_BATCH_FILES = 30
_ACTIVE = (OcrStatus.PENDING.value, OcrStatus.RUNNING.value)


@dataclass(frozen=True)
class BatchItemResult:
    filename: str
    status: str
    invoice_id: int | None = None
    job_id: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class BatchCreated:
    batch_id: UUID
    items: list[BatchItemResult]
    created: int
    duplicates: int
    rejected: int


def create_batch(db: Session, user: User, uploads: list, *, settings: Settings,
                 storage: StorageService) -> BatchCreated:
    if not 1 <= len(uploads) <= MAX_BATCH_FILES:
        raise ValidationFailedError(f"一次批量上传 1–{MAX_BATCH_FILES} 个文件")
    batch_id = uuid4()
    items: list[BatchItemResult] = []
    created = duplicates = rejected = 0
    for upload in uploads:
        try:
            data = upload_service.upload_invoice(db, user, upload, settings=settings, storage=storage)
        except DuplicateInvoiceError as exc:
            duplicates += 1
            items.append(BatchItemResult(upload.filename or "-", "duplicate",
                                         invoice_id=exc.detail.get("invoice_id"),
                                         error="影像已上传"))
            continue
        except AppError as exc:  # 单个文件失败不拖垮整批（DB/程序错误仍应抛出）
            rejected += 1
            items.append(BatchItemResult(upload.filename or "-", "rejected",
                                         error=getattr(exc, "message", "上传失败")))
            continue
        invoice = db.get(Invoice, data.invoice_id)
        job = db.get(OcrJob, data.job_id)
        invoice.batch_id = batch_id
        job.dispatched = False              # 排队，由 dispatch_pending 统一入队
        db.commit()
        created += 1
        items.append(BatchItemResult(upload.filename or "-", "created",
                                     invoice_id=invoice.id, job_id=job.id))
    return BatchCreated(batch_id, items, created, duplicates, rejected)


async def dispatch_pending(db: Session, uploader_id: int, *, queue, settings: Settings) -> int:
    active = db.scalar(
        select(func.count()).select_from(OcrJob)
        .join(Invoice, Invoice.id == OcrJob.invoice_id)
        .where(Invoice.uploader_id == uploader_id, OcrJob.dispatched.is_(True),
               OcrJob.status.in_(_ACTIVE))
    ) or 0
    slots = settings.ocr_max_active_per_user - active
    if slots <= 0:
        return 0
    jobs = db.scalars(
        select(OcrJob).join(Invoice, Invoice.id == OcrJob.invoice_id)
        .where(Invoice.uploader_id == uploader_id, OcrJob.dispatched.is_(False),
               OcrJob.status == OcrStatus.PENDING.value)
        .order_by(OcrJob.id).limit(slots)
    ).all()
    moved = 0
    for job in jobs:
        job.dispatched = True
        db.commit()
        try:
            await queue.enqueue_ocr(job.id)
        except Exception:
            await asyncio.to_thread(job_service.mark_enqueue_failed, job.id, db=db)
        moved += 1
    return moved


async def dispatch_for_job(job_id: int, *, queue, settings: Settings) -> int:
    """worker `after_job_end`：为结束作业所属用户补位。"""
    from app.core.database import get_session_factory

    with get_session_factory()() as db:
        job = db.get(OcrJob, job_id)
        if job is None:
            return 0
        invoice = db.get(Invoice, job.invoice_id)
        if invoice is None:
            return 0
        return await dispatch_pending(db, invoice.uploader_id, queue=queue, settings=settings)
```

- [ ] **Step 3: 验证**：`../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_batch.py`

- [ ] **Step 4: 提交**

```bash
git add backend/app/services/batch_service.py backend/tests/test_batch.py
git commit -m "feat(s3-8): 批量建单与服务端排队（单用户并发 3）"
```

---

### Task 3: 三接口 + worker 补位

**Files:**
- Modify: `backend/app/api/v1/invoices.py`、`backend/app/schemas/invoice.py`、`backend/app/tasks/worker.py`、`docs/API.md`
- Test: `backend/tests/test_batch.py`（补 HTTP）、`tests/test_scheduler.py`（worker 钩子存在性可选）

- [ ] **Step 1: schema**

```python
class BatchUploadItemOut(BaseModel):
    filename: str
    status: Literal["created", "duplicate", "rejected"]
    invoice_id: int | None = None
    job_id: int | None = None
    error: str | None = None


class BatchUploadData(BaseModel):
    batch_id: UUID
    created: int
    duplicates: int
    rejected: int
    items: list[BatchUploadItemOut]


class BatchInvoiceItemOut(BaseModel):
    invoice_id: int
    filename: str
    ocr_status: str
    extract_status: str
    progress: int
    error: str | None = None


class BatchSummaryData(BaseModel):
    batch_id: UUID
    total: int
    counts: dict[str, int]
    items: list[BatchInvoiceItemOut]
    created_at: datetime


class BatchRetryData(BaseModel):
    invoice_id: int
    job_id: int
```

- [ ] **Step 2: service 补 `batch_summary` / `retry_item`**

```python
def batch_summary(db: Session, user: User, batch_id: UUID) -> BatchSummaryData:
    stmt = select(Invoice).where(Invoice.batch_id == batch_id)
    if user.role != UserRole.REVIEWER.value:
        stmt = stmt.where(Invoice.uploader_id == user.id)
    invoices = db.scalars(stmt.order_by(Invoice.id)).all()
    if not invoices:
        raise NotFoundError("批次不存在")
    items, counts = [], {"pending": 0, "running": 0, "succeeded": 0, "failed": 0}
    for invoice in invoices:
        file = db.get(InvoiceFile, invoice.file_id)
        job = db.scalar(select(OcrJob).where(OcrJob.invoice_id == invoice.id)
                        .order_by(OcrJob.id.desc()).limit(1))
        stages = ocr_status_service.build_stages(job, (job.metrics or {}) if job else {})
        progress = ocr_status_service.compute_progress(
            [stage.model_dump() for stage in stages], invoice.ocr_status)
        counts[invoice.ocr_status] = counts.get(invoice.ocr_status, 0) + 1
        items.append(BatchInvoiceItemOut(
            invoice_id=invoice.id, filename=file.filename if file else "-",
            ocr_status=invoice.ocr_status, extract_status=invoice.extract_status,
            progress=progress, error=job.error if job else None))
    return BatchSummaryData(batch_id=batch_id, total=len(items), counts=counts,
                            items=items, created_at=invoices[0].created_at)


def retry_item(db, user, batch_id, invoice_id) -> tuple[int, int]:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.batch_id != batch_id:
        raise NotFoundError("批次项不存在")
    if invoice.uploader_id != user.id:
        raise NotFoundError("批次项不存在")
    active = db.scalar(select(func.count()).select_from(OcrJob).where(
        OcrJob.invoice_id == invoice.id, OcrJob.status.in_(_ACTIVE)))
    if active:
        raise ConflictError("该票据已有进行中的识别任务")
    job = job_service.create_job(db, invoice_id=invoice.id, status=OcrStatus.PENDING.value)
    job.dispatched = False
    invoice.ocr_status = OcrStatus.PENDING.value
    db.commit()
    return invoice.id, job.id
```

- [ ] **Step 3: 路由**

```python
@router.post(
    "/batch",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Envelope[BatchUploadData],
    summary="批量上传（1–30 文件；全部异步识别）",
)
async def upload_batch(
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
    queue: OcrTaskQueue = Depends(get_task_queue),
) -> dict:
    created = await asyncio.to_thread(
        batch_service.create_batch, db, user, files, settings=settings, storage=storage
    )
    await batch_service.dispatch_pending(
        db, user.id, queue=queue, settings=settings
    )
    return ok(
        {
            "batch_id": str(created.batch_id),
            "created": created.created,
            "duplicates": created.duplicates,
            "rejected": created.rejected,
            "items": [asdict(item) for item in created.items],
        },
        "accepted",
    )
```

> 注意：`POST /invoices/batch` 的路径是 `/invoices/batch`，与 `/{invoice_id}` 存在路由顺序冲突
> （`batch` 会被当成 `invoice_id`）——必须把 batch 路由**定义在** `GET /{invoice_id}` 等动态路由之前，
> 或统一放到独立 router 前缀下。测试必须覆盖 `POST /invoices/batch` 不被 `/{invoice_id}` 抢先匹配。

```python
@router.get(
    "/batch/{batch_id}",
    response_model=Envelope[BatchSummaryData],
    summary="批量上传汇总与逐项进度",
)
async def get_batch(
    batch_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    queue: OcrTaskQueue = Depends(get_task_queue),
) -> dict:
    try:
        parsed = UUID(batch_id)
    except ValueError as exc:
        raise ValidationFailedError("batch_id 不是合法 UUID") from exc
    summary = await asyncio.to_thread(batch_service.batch_summary, db, user, parsed)
    await batch_service.dispatch_pending(db, user.id, queue=queue, settings=settings)
    return ok(summary.model_dump(mode="json"))

@router.post(
    "/batch/{batch_id}/items/{invoice_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Envelope[BatchRetryData],
    summary="失败批次项重试（原文件重投，不建新单）",
)
async def retry_batch_item(
    batch_id: str,
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    queue: OcrTaskQueue = Depends(get_task_queue),
) -> dict:
    try:
        parsed = UUID(batch_id)
    except ValueError as exc:
        raise ValidationFailedError("batch_id 不是合法 UUID") from exc
    new_invoice_id, new_job_id = await asyncio.to_thread(
        batch_service.retry_item, db, user, parsed, invoice_id
    )
    await batch_service.dispatch_pending(db, user.id, queue=queue, settings=settings)
    return ok({"invoice_id": new_invoice_id, "job_id": new_job_id}, "accepted")
```

（`batch_id` 非法 UUID → 422；`GET` 404 语义同票据。）

- [ ] **Step 4: worker 钩子**

```python
    @staticmethod
    async def after_job_end(ctx: dict, job) -> None:
        from app.services import batch_service

        if not job.args:
            return
        try:
            await batch_service.dispatch_for_job(int(job.args[0]), queue=ctx["queue"],
                                                 settings=get_settings())
        except Exception:
            logger.warning("补位失败", extra={"event": "batch_dispatch_failed"})
```

`on_startup` 里：`ctx["queue"] = ArqOcrTaskQueue(RedisSettings.from_dsn(get_settings().redis_url))`。

- [ ] **Step 5: API.md**：新增三个接口（spec §3.3 原文），并注明"batch 路由需定义在 `/{invoice_id}` 之前"。

- [ ] **Step 6: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_batch.py
../.venv/Scripts/python.exe -m pytest -q tests/test_api_docs.py
```

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/batch_service.py backend/app/schemas/invoice.py \
  backend/app/api/v1/invoices.py backend/app/tasks/worker.py docs/API.md backend/tests/test_batch.py
git commit -m "feat(s3-8): 批量接口、进度汇总与失败项重试"
```

---

### Task 4: 前端批量模式

**Files:** `web/src/api/{files,invoices}.ts`、`web/src/types/invoice.ts`、`web/src/views/Upload.vue`

- [ ] **Step 1: API/类型**

```ts
// types/invoice.ts
export interface BatchUploadItem {
  filename: string
  status: 'created' | 'duplicate' | 'rejected'
  invoice_id: number | null
  job_id: number | null
  error: string | null
}
export interface BatchUploadData {
  batch_id: string
  created: number
  duplicates: number
  rejected: number
  items: BatchUploadItem[]
}
export interface BatchInvoiceItem {
  invoice_id: number
  filename: string
  ocr_status: OcrStatus
  extract_status: string
  progress: number
  error: string | null
}
export interface BatchSummaryData {
  batch_id: string
  total: number
  counts: Record<string, number>
  items: BatchInvoiceItem[]
  created_at: string
}
```

```ts
// api/files.ts
export async function uploadBatch(
  files: File[], onProgress?: (percent: number) => void,
): Promise<BatchUploadData> {
  const form = new FormData()
  files.forEach((file) => form.append('files', file))
  const response = await http.post<Envelope<BatchUploadData>>('/invoices/batch', form, {
    timeout: 120_000,
    onUploadProgress: (event) => {
      if (event.total) onProgress?.(Math.round((event.loaded / event.total) * 100))
    },
  })
  return unwrap(response.data)
}

// api/invoices.ts
export async function fetchBatch(batchId: string): Promise<BatchSummaryData> { ... }
export async function retryBatchItem(batchId: string, invoiceId: number): Promise<void> { ... }
```

- [ ] **Step 2: Upload.vue 批量模式**

- 文件数 >1：调用 `uploadBatch`；单文件保持现有串行路径；
- 上传中显示总上传进度；上传完成得到 `batch_id` + items（含 duplicate/rejected）后进入轮询：
  每 3s `fetchBatch`，总进度 = `(succeeded + failed) / total * 100`；
- 列表逐项：文件名、状态 tag、progress；`failed` → "重试"按钮调 retry 后继续轮询；
- duplicate → "已上传 + 查看"链接；rejected → 红色原因；
- 全部结束后停止定时器（`onBeforeUnmount` 清理）。

- [ ] **Step 3: 验证**：`npm run typecheck && npm run build`

- [ ] **Step 4: 提交**

```bash
git add web/src/api/files.ts web/src/api/invoices.ts web/src/types/invoice.ts web/src/views/Upload.vue
git commit -m "feat(s3-8): 上传页批量模式（总进度/逐项状态/失败重试）"
```

---

### Task 5: 30 文件冒烟 + 回归 + 文档

- [ ] **Step 1: 全量验证**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
```

- [ ] **Step 2: 30 文件真实冒烟**

```bash
../.venv/Scripts/python.exe ../scripts/create_user.py --username s38sub --role submitter \
  --real-name S38批 --password 'S38sub!2026'
# 1) 上传 30 张不同样本（若样本不足，用 PIL 轻微改像素生成第 2 组）
# 2) 轮询 GET batch，直到 total 全部终态；记录耗时、并发 3 的入队数、最终 counts
# 3) 用 DB 把其中 1 个 job 置 failed（模拟识别失败）→ retry 接口 → 确认 invoice 总数不变
# 4) 清理票据/对象/账号
```

- [ ] **Step 3: 文档**：PROGRESS（勾 S3-8 + 实测：30 文件耗时、并发、重试前后 invoice 数）、
  DECISIONS **D-067**、AGENTS 锚点、HANDOFF（下一步 S3-9 若有，否则阶段 3 收尾 + 阶段 4）、
  API.md 三接口已完成标记、spec/plan 状态改"已实现"。

- [ ] **Step 4: 提交**

```bash
git add docs AGENTS.md
git commit -m "docs(s3-8): 批量上传口径与实测收口"
```

---

## 自检（对照 spec）

| spec 条目 | 覆盖 Task |
|---|---|
| §1 产出 1–9 | Task 1–5 |
| §2 Q1/Q2/Q3 | Task 1/2/3 |
| §3.1 迁移 | Task 1 |
| §3.2 batch_service | Task 2/3 |
| §3.3 接口 | Task 3 |
| §3.4 worker 补位 | Task 3 |
| §3.5 前端 | Task 4 |
| §4 测试 | Task 2/3/5 |
| §5 验收 | Task 5 |

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 入队失败（Redis 挂） | 已建单保留，job 置 failed；用户可在批量页对失败项重试 |
| 30 文件超过 Nginx 限制 | 接口不做同步识别；若部署仍限 body 大小，调 `client_max_body_size`（文档写明） |
| 并发补位不生效 | 检查 `after_job_end` 是否注册、`dispatched` 是否正确；`GET batch` 也会补位兜底 |
| 迁移回退 | `alembic downgrade -1` 删两列；历史 `dispatched` 默认 true 语义不变 |