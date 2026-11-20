# S3-1 OCR 流水线实现计划（plan）

> 对应 spec：[`docs/spec/s3-1-ocr-pipeline.md`](../spec/s3-1-ocr-pipeline.md)（已确认）｜
> 契约：`docs/API.md` §2、`docs/SCHEMA.md` §3.1–3.5 ｜ 状态：**待执行**
> 执行方式：按 Task 1→9 顺序，每个 Task 自带验证命令；每个 Task 结束时跑对应测试，不绿不进入下一 Task。

**目标**：上传后的 ARQ 作业真正跑通 下载→渲染/预处理→检测识别→票种→抽取→校验→落库，
`invoices.ocr_status` 真实推进，`GET /invoices/{id}/ocr-status` 可轮询。

**架构**：`services/ocr_pipeline.py` 是唯一编排入口（依赖可注入：engine / storage / prepare_pages / templates / clock）；
`services/job_service.py` 只负责状态机与终态守卫；`ocr/` 各模块保持既有契约不动；前端只换轮询接口。

**技术栈**：Python 3.11 / FastAPI / SQLAlchemy 2.0 / Alembic / ARQ / PaddleOCR 2.7.3（CPU）/ PyMuPDF（PDF 渲染）/ Vue 3 + TS。

## Global Constraints（每个 Task 都适用）

- 所有 Python 命令用 `.venv/Scripts/python.exe`；工作目录 `E:\BiSheXZY\study-OCR\backend`（除非注明）。
- **不改**已提交的迁移（0001–0003）；新列只走新 revision `0004`。
- **不新增依赖**：PDF 用既有 PyMuPDF（`ocr/intake.py` 已用），不引 `pypdfium2`。
- 金额一律 `Decimal` + `NUMERIC(12,2)`；时间存 UTC（`datetime.now(timezone.utc)`），接口输出带时区。
- 日志与 `ocr_jobs.error` **禁止**出现票面明文/金额/税号/对象名；错误文本 ≤500 字符。
- 测试不得真实调用模型/MinIO/Redis（`-m integration` 用临时库 + 假实现）。
- 文档同步：动表改 `SCHEMA.md`、动接口改 `API.md`、动配置改 `.env.example`、收口改 `PROGRESS.md` + `DECISIONS.md`。
- 提交信息 Conventional Commits（含任务号 `s3-1`）；**push 由用户执行**。

## 文件结构（谁负责什么）

| 文件 | 职责 |
|---|---|
| `backend/app/services/ocr_pipeline.py`（新） | 编排 + 计时 + 落库；纯函数（合并/行构造/进度）可单测 |
| `backend/app/services/ocr_status_service.py`（新） | `ocr-status` 读模型：`stages`/`progress`/权限 |
| `backend/app/services/job_service.py` | 加 `started_at/finished_at`、`superseded` 终态守卫、`AbortAttempt` |
| `backend/app/services/storage.py` | 加 `get_bytes` |
| `backend/app/ocr/intake.py` | 加 `PreparedPage` + `prepare_pages`（PDF 前 3 页） |
| `backend/app/ocr/extractor.py` | 加公开 `rules_from_specs`（DB 规格 → FieldRule） |
| `backend/app/tasks/ocr_task.py` | `_run_pipeline` 委托 `ocr_pipeline.run_pipeline` |
| `backend/app/tasks/worker.py` | `max_jobs` 可配（默认 1） |
| `backend/app/schemas/invoice.py` | `OcrStageOut` / `OcrStatusData` |
| `backend/app/api/v1/invoices.py` | `GET /{invoice_id}/ocr-status` |
| `backend/app/models/ocr_jobs.py` + `alembic/versions/0004_*` | 三列 |
| `backend/tests/{test_ocr_pipeline,test_ocr_status,test_storage}.py` | 新增；`test_jobs.py`/`test_intake.py`/`test_extractor.py` 补用例 |
| `scripts/ocr_acceptance.py`（新） | 真实 20 张验收 + `--cleanup` |
| `web/src/{types/invoice.ts,api/invoices.ts,views/Upload.vue}` | 轮询换 `ocr-status` |

---

### Task 1: 契约与数据层（SCHEMA + 模型 + 迁移 0004）

**Files:**
- Modify: `docs/SCHEMA.md` §3.5（`ocr_jobs` 表格 + 说明段）
- Modify: `backend/app/models/ocr_jobs.py`
- Create: `backend/alembic/versions/0004_ocr_jobs_metrics_and_times.py`

**Interfaces:**
- Produces: `OcrJob.metrics: dict | None`、`OcrJob.started_at: datetime | None`、`OcrJob.finished_at: datetime | None`

- [ ] **Step 1: 先改 SCHEMA（契约先行）**

在 `docs/SCHEMA.md` §3.5 表格末尾（`created_at` 之后）追加：

```markdown
| metrics | JSONB | 是 | — | 逐阶段状态与耗时（`metrics.stages`）、引擎参数、`warnings`；**不含票面明文** ✅ | — |
| started_at | TIMESTAMPTZ | 是 | — | 本次尝试开始时间（`job_service` 在 `running` 时写，重试时覆盖）✅ | — |
| finished_at | TIMESTAMPTZ | 是 | — | 进入终态（`succeeded/failed`）的时间；回 `pending` 重试时清空 ⚠ | — |
```

把「关键不变式」段里"同一 `(invoice_id, stage)` 的重试追加新行"改为下述口径（D-055）：

```markdown
**关键不变式**：**一次运行只有一行**（1 个 ARQ 作业 = 1 行）——表里没有 `run_id`，多行无法归组到同一次
运行；`stage` 记录当前/最后阶段，逐阶段状态与耗时写 `metrics.stages`。重试仍复用该行（`retry_count` 记第几次尝试）。
```

在「提交语义的例外」段把豁免扩到两处：

```markdown
> **提交语义的例外**（D-055）：`ocr_jobs` 的 `stage/metrics` 与 `invoices.ocr_status='running'` 允许中途提交，
> 理由都是"阶段进度必须进行中可见"。其余业务列只在最后一次事务提交，失败一律回滚。
```

- [ ] **Step 2: 改模型**

`backend/app/models/ocr_jobs.py`，把 `from sqlalchemy import (...)` 的导入补 `DateTime`（已存在），在 `created_at` 之前加：

```python
    # 逐阶段状态/耗时/引擎参数/警告；不含明文（SCHEMA §3.5）
    metrics: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)
    # 本次尝试开始/终态结束（ocr-status 契约要求真时间，D-056）
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 3: 生成迁移 0004**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic revision --autogenerate --rev-id 0004 -m "ocr_jobs_metrics_and_times"
```

人工检查生成的 revision：`revision: str = "0004"`、`down_revision = "0003"`，只含三列 `add_column`，
`downgrade()` 只 `drop_column` 三列；文件名统一为 `0004_ocr_jobs_metrics_and_times.py`。
**不要**改 `revision` 常量（Alembic 只认它）。

若 autogenerate 不可用（库未起），手写：

```python
"""ocr_jobs 增加 metrics / started_at / finished_at（S3-1，D-056）"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ocr_jobs", sa.Column("metrics", postgresql.JSONB(), nullable=True))
    op.add_column("ocr_jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ocr_jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("ocr_jobs", "finished_at")
    op.drop_column("ocr_jobs", "started_at")
    op.drop_column("ocr_jobs", "metrics")
```

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m alembic upgrade head     # 输出 head = 0004
../.venv/Scripts/python.exe -m alembic check            # "No new upgrade operations detected"
../.venv/Scripts/python.exe -m pytest -q tests/test_models.py tests/test_migration.py
```

- [ ] **Step 5: 提交**

```bash
git add docs/SCHEMA.md backend/app/models/ocr_jobs.py backend/alembic/versions/0004_ocr_jobs_metrics_and_times.py
git commit -m "feat(s3-1): ocr_jobs 增加 metrics/started_at/finished_at（0004）"
```

---

### Task 2: `job_service` 写时间戳 + 终态守卫（TDD）

**Files:**
- Modify: `backend/app/services/job_service.py`
- Test: `backend/tests/test_jobs.py`（追加 3 条集成用例）

**Interfaces:**
- Produces: `AbortAttempt`（非可重试；`job_service` 视作 `superseded`）；`execute_job_attempt` 返回新增值 `"superseded"`；
  终态前校验 `(status, retry_count)`。

- [ ] **Step 1: 写失败测试（追加到 `tests/test_jobs.py` 集成区）**

```python
def test_execute_job_attempt_records_started_and_finished(session_factory, owner, invoice, job):
    job_service.execute_job_attempt(
        job.id, attempt=1, handler=lambda db, row: None,
        session_factory=session_factory, lock_store=FakeLockStore(),
    )
    with session_factory() as db:
        row = db.get(OcrJob, job.id)
        assert row.status == "succeeded"
        assert row.started_at is not None and row.finished_at is not None
        assert row.finished_at >= row.started_at


def test_execute_job_attempt_retry_clears_finished_at(session_factory, owner, invoice, job):
    def boom(db, row):
        raise OSError("磁盘临时错误")

    with pytest.raises(job_service.RetryLater):
        job_service.execute_job_attempt(
            job.id, attempt=1, handler=boom,
            session_factory=session_factory, lock_store=FakeLockStore(),
        )
    with session_factory() as db:
        row = db.get(OcrJob, job.id)
        assert row.status == "pending"
        assert row.finished_at is None          # 回到 pending 不写结束时间


def test_execute_job_attempt_superseded_does_not_commit(session_factory, owner, invoice, job):
    """handler 写完业务列后才发现作业已被别的执行改掉 → 回滚业务写入、状态不变。"""

    def handler(db, row):
        db.execute(
            sa_text("UPDATE invoices SET ocr_status = 'running' WHERE id = :id"),
            {"id": invoice.id},
        )
        db.execute(
            sa_text("UPDATE ocr_jobs SET status = 'failed', retry_count = 9 WHERE id = :id"),
            {"id": job.id},
        )

    outcome = job_service.execute_job_attempt(
        job.id, attempt=1, handler=handler,
        session_factory=session_factory, lock_store=FakeLockStore(),
    )
    assert outcome == "superseded"
    with session_factory() as db:
        assert db.get(Invoice, invoice.id).ocr_status == "pending"   # 业务写入被回滚
        assert db.get(OcrJob, job.id).status == "failed"             # 别人的状态没被覆盖
        assert db.get(OcrJob, job.id).retry_count == 9
```

（`invoice`/`job` 夹具已存在于 `test_jobs.py`；如字段名不同，按现有夹具调整，**不要**改夹具语义。）

- [ ] **Step 2: 运行确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_jobs.py -k "started or retry_clears or superseded"
```

预期：`AttributeError: ... started_at` 或 `outcome == "succeeded"`（不是 superseded）。

- [ ] **Step 3: 实现**

`job_service.py`：

```python
class AbortAttempt(RuntimeError):
    """作业已不在 running（被别的执行接管/已终态）：不重试、不写状态。"""

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
```

`execute_job_attempt` 中：

```python
            job.status = OcrStatus.RUNNING.value
            job.retry_count = attempt
            job.error = None
            job.started_at = _utcnow()
            job.finished_at = None
            db.commit()
            db.refresh(job)
```

handler 成功之后、写终态之前插守卫：

```python
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
```

`_persist_failure` 开头（`db.get(OcrJob, job_id)` 之后）加：

```python
    if isinstance(exc, AbortAttempt) or job.status != OcrStatus.RUNNING.value or job.retry_count != attempt:
        return "superseded"
```

并在三处写状态时补 `finished_at`：回 `pending` → `job.finished_at = None`；终态 `failed` → `job.finished_at = _utcnow()`。

- [ ] **Step 4: 跑测试**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_jobs.py
../.venv/Scripts/python.exe -m pytest -q tests/test_jobs.py      # 单元部分不回归
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/job_service.py backend/tests/test_jobs.py
git commit -m "feat(s3-1): 作业尝试记录 started_at/finished_at，终态前用状态指纹防覆写"
```

---

### Task 3: `storage.get_bytes` + `intake.prepare_pages`

**Files:**
- Modify: `backend/app/services/storage.py`
- Modify: `backend/app/ocr/intake.py`
- Test: `backend/tests/test_storage.py`（新）、`backend/tests/test_intake.py`（追加）

**Interfaces:**
- Produces:
  - `StorageService.get_bytes(key: str, *, max_bytes: int) -> bytes`（缺失 → `NotFoundError`，不可用 → `ServiceUnavailableError`，超限 → `PayloadTooLargeError`）
  - `PreparedPage(page_no, image, kind, quality_flag, needs_retake, retake_hints, notes, preprocess)`
  - `intake.prepare_pages(source, options=None, *, max_pages=3, pdf_dpi=250) -> list[PreparedPage]`

- [ ] **Step 1: 写失败测试**

`tests/test_storage.py`：

```python
"""storage.get_bytes 单测（S3-1）：假 MinIO client，不连真实对象存储。"""
from __future__ import annotations

import io

import pytest
from minio.error import S3Error

from app.core.exceptions import NotFoundError, PayloadTooLargeError, ServiceUnavailableError
from app.services.storage import StorageService


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._buffer = io.BytesIO(payload)
        self.closed = False
        self.released = False

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeClient:
    def __init__(self, payload: bytes | None, error: S3Error | None = None) -> None:
        self.payload = payload
        self.error = error
        self.last_response: FakeResponse | None = None

    def get_object(self, bucket, key):
        if self.error is not None:
            raise self.error
        self.last_response = FakeResponse(self.payload or b"")
        return self.last_response


def _s3_error(code: str) -> S3Error:
    return S3Error(code, "boom", None, None, None, None)


def test_get_bytes_returns_payload_and_releases_connection() -> None:
    client = FakeClient(b"hello")
    storage = StorageService(client, "bucket")
    assert storage.get_bytes("invoices/2026/11/a.bin", max_bytes=1024) == b"hello"
    assert client.last_response.closed and client.last_response.released


def test_get_bytes_missing_object_raises_not_found() -> None:
    storage = StorageService(FakeClient(None, _s3_error("NoSuchKey")), "bucket")
    with pytest.raises(NotFoundError):
        storage.get_bytes("invoices/2026/11/missing.bin", max_bytes=1024)


def test_get_bytes_unavailable_raises_503() -> None:
    storage = StorageService(FakeClient(None, _s3_error("InternalError")), "bucket")
    with pytest.raises(ServiceUnavailableError):
        storage.get_bytes("invoices/2026/11/x.bin", max_bytes=1024)


def test_get_bytes_over_limit_raises_413() -> None:
    storage = StorageService(FakeClient(b"x" * 2048), "bucket")
    with pytest.raises(PayloadTooLargeError):
        storage.get_bytes("invoices/2026/11/big.bin", max_bytes=1024)
```

`tests/test_intake.py` 追加：

```python
def _write_multipage_pdf(path, pages: int = 3) -> None:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        pytest.skip("PyMuPDF 不可用")
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 100), f"第 {index + 1} 页")
    document.save(str(path))
    document.close()


def test_prepare_pages_pdf_returns_all_pages(tmp_path) -> None:
    pdf = tmp_path / "multi.pdf"
    _write_multipage_pdf(pdf, pages=3)
    pages = prepare_pages(pdf)
    assert [page.page_no for page in pages] == [1, 2, 3]
    assert all(page.kind == "pdf" and not page.image.size == 0 for page in pages)


def test_prepare_pages_pdf_caps_max_pages(tmp_path) -> None:
    pdf = tmp_path / "multi.pdf"
    _write_multipage_pdf(pdf, pages=3)
    assert len(prepare_pages(pdf, max_pages=2)) == 2


def test_prepare_pages_photo_returns_single_page(tmp_path) -> None:
    photo = tmp_path / "shot.jpg"
    _write_photo(photo)
    pages = prepare_pages(photo)
    assert len(pages) == 1
    assert pages[0].kind == "photo" and pages[0].quality_flag in {"ok", "low"}
```

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_storage.py tests/test_intake.py -k "get_bytes or prepare_pages"
```

- [ ] **Step 3: 实现 `storage.get_bytes`**

```python
    def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        """读取对象完整字节；缺失/不可用/超限分别抛业务异常（S3-1 流水线用）。"""
        try:
            response = self._client.get_object(self._bucket, key)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NoSuchBucket"):
                raise NotFoundError("票据文件对象不存在") from exc
            raise ServiceUnavailableError("对象存储不可用") from exc
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PayloadTooLargeError("票据文件超过读取上限")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()
            response.release_conn()
```

模块顶部补 `_CHUNK_SIZE = 1024 * 1024` 与 `from app.core.exceptions import (NotFoundError, PayloadTooLargeError, ServiceUnavailableError)`。

- [ ] **Step 4: 实现 `intake.prepare_pages`**

```python
@dataclass
class PreparedPage:
    page_no: int
    image: np.ndarray
    kind: str                      # "pdf" | "photo"
    quality_flag: str | None       # PDF 不预处理 → None（不编造质量结论）
    needs_retake: bool = False
    retake_hints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    preprocess: PreprocessResult | None = None
```

把 `_render_pdf(path, dpi)` 改为 `_render_pdf(path, dpi, max_pages=None) -> tuple[list[np.ndarray], int]`：
在 `with fitz.open(...)` 内循环 `document.load_page(index)`，`index < min(page_count, max_pages or page_count)`；
`prepare_input` 改成 `images, pages = _render_pdf(path, pdf_dpi); image = images[0]`（行为不变）。

```python
def prepare_pages(source: str | Path | bytes, options: PreprocessOptions | None = None,
                  *, max_pages: int = 3, pdf_dpi: int = DEFAULT_PDF_DPI) -> list[PreparedPage]:
    """PDF：渲染前 max_pages 页（不预处理，D-035）；照片：预处理后单页。"""
    kind = detect_input_kind(source)
    if kind == "photo":
        prepared = prepare_input(source, options)
        quality = prepared.preprocess.quality_flag if prepared.preprocess is not None else None
        return [PreparedPage(page_no=1, image=prepared.image, kind="photo", quality_flag=quality,
                             needs_retake=prepared.needs_retake, retake_hints=prepared.retake_hints,
                             notes=prepared.notes, preprocess=prepared.preprocess)]

    if isinstance(source, (bytes, bytearray)):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(bytes(source))
            temp_path = Path(handle.name)
        try:
            images, _ = _render_pdf(temp_path, pdf_dpi, max_pages=max_pages)
        finally:
            temp_path.unlink(missing_ok=True)
    else:
        images, _ = _render_pdf(Path(source), pdf_dpi, max_pages=max_pages)
    return [
        PreparedPage(page_no=index + 1, image=image, kind="pdf", quality_flag=None,
                     notes=[f"pdf_rendered:{pdf_dpi}dpi", "pdf_skip_preprocess:电子件不做预处理（D-035）"])
        for index, image in enumerate(images)
    ]
```

`__all__` 补 `"PreparedPage", "prepare_pages"`。

- [ ] **Step 5: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_storage.py tests/test_intake.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/storage.py backend/app/ocr/intake.py backend/tests/test_storage.py backend/tests/test_intake.py
git commit -m "feat(s3-1): storage.get_bytes 与 intake.prepare_pages（PDF 前 3 页）"
```

---

### Task 4: `extractor.rules_from_specs`（DB 规格 → FieldRule）

**Files:**
- Modify: `backend/app/ocr/extractor.py`
- Modify: `backend/app/services/template_service.py`（`_to_field_rules` 改为委托，行为不变）
- Test: `backend/tests/test_extractor.py`（追加）

**Interfaces:**
- Produces: `extractor.rules_from_specs(specs: dict[str, dict]) -> list[FieldRule]`
  （与 `load_field_rules()` 对同一份 YAML 逐字段等价；`rule_id = f"{name}:{value_type}"`）

- [ ] **Step 1: 写失败测试（`tests/test_extractor.py` 末尾）**

```python
def test_rules_from_specs_matches_yaml_rules() -> None:
    """DB 版规则（field_specs_json）与 YAML 版规则必须逐字段等价，否则两套来源会漂移。"""
    from app.ocr.extractor import load_field_rules, rules_from_specs
    from app.services.template_seed import build_seed_templates

    yaml_rules = {rule.name: rule for rule in load_field_rules()}
    seed = next(item for item in build_seed_templates() if item.type_code == "vat_digital")
    db_rules = {rule.name: rule for rule in rules_from_specs(seed.field_specs_json)}

    assert db_rules, "vat_digital 的 DB 规则不能为空"
    for name, db_rule in db_rules.items():
        expected = yaml_rules[name]
        assert db_rule.value_type == expected.value_type
        assert db_rule.anchors == expected.anchors
        assert db_rule.direction == expected.direction
        assert db_rule.regex == expected.regex
        assert db_rule.max_distance == expected.max_distance
        assert db_rule.zone == expected.zone
        assert db_rule.occurrence == expected.occurrence
        assert db_rule.required == expected.required
        assert db_rule.if_multiple == expected.if_multiple
        assert db_rule.value_index == expected.value_index
        assert db_rule.exclude == expected.exclude
        assert db_rule.whole_line_anchors == expected.whole_line_anchors
        assert db_rule.rule_id == expected.rule_id
```

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_extractor.py -k rules_from_specs
```

预期 `ImportError: cannot import name 'rules_from_specs'`。

- [ ] **Step 3: 实现并去重**

把 `template_service._to_field_rules` 的函数体搬进 `extractor.py`，改名 `rules_from_specs`，
`_STRATEGY_TO_DIRECTION` 常量一并搬过去（`template_service` 里删掉该常量与函数体，保留薄封装）：

```python
# extractor.py
_STRATEGY_TO_DIRECTION = {
    "inline_right": "right",
    "below_center": "below",
    "table_column": "any",
    "label_neighbor": "any",
    "total_zone": "any",
}


def rules_from_specs(specs: dict[str, Any]) -> list[FieldRule]:
    """DB 的 ticket_templates.field_specs_json → FieldRule（S2-8 起规则读库，S3-1 流水线用）。"""
    rules: list[FieldRule] = []
    for name, spec in (specs or {}).items():
        if not isinstance(spec, dict):
            continue
        keywords = spec.get("keywords") or spec.get("anchors") or []
        direction = spec.get("direction") or _STRATEGY_TO_DIRECTION.get(
            str(spec.get("strategy") or ""), "right"
        )
        zone = spec.get("zone")
        rules.append(FieldRule(
            name=name,
            value_type=str(spec.get("value_type", "text")),
            anchors=[str(item) for item in keywords],
            direction=direction,
            regex=spec.get("regex") or spec.get("pattern"),
            max_distance=float(spec.get("max_distance") or _DEFAULT_MAX_DISTANCE),
            zone=(float(zone[0]), float(zone[1])) if zone else None,
            occurrence=str(spec.get("occurrence", "all")),
            tickets=spec.get("tickets"),
            required=bool(spec.get("required", False)),
            if_multiple=spec.get("if_multiple"),
            value_index=int(spec.get("value_index", 0)),
            exclude=spec.get("exclude"),
            whole_line_anchors=spec.get("whole_line_anchors"),
            rule_id=f"{name}:{spec.get('value_type', 'text')}",
        ))
    return rules
```

注意：`max_distance` 必须回退到 `_DEFAULT_MAX_DISTANCE`（300.0），与 YAML 缺省一致；
seed 的 `_spec_from_rule` 会写 `max_distance: None`，`float(None or 300.0)` 才等于 YAML 默认。

`template_service.py`：

```python
def _to_field_rules(specs: dict[str, Any]) -> list:
    from app.ocr.extractor import rules_from_specs  # 延迟导入

    return rules_from_specs(specs)
```

- [ ] **Step 4: 验证（含模板试跑不回归）**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_extractor.py tests/test_templates.py tests/test_layout.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/ocr/extractor.py backend/app/services/template_service.py backend/tests/test_extractor.py
git commit -m "feat(s3-1): extractor.rules_from_specs 统一 YAML/DB 两套规则来源"
```

---

### Task 5: 配置项 + worker 并发

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/tasks/worker.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.ocr_pipeline_deadline_seconds: int = 165`、`ocr_pdf_max_pages: int = 3`、
  `ocr_worker_max_jobs: int = 1`（校验 `1 <= x <= 2`）；`WorkerSettings.max_jobs`

- [ ] **Step 1: 写失败测试（`tests/test_config.py` 追加）**

```python
def test_ocr_pipeline_settings_defaults(settings) -> None:
    assert settings.ocr_pipeline_deadline_seconds == 165
    assert settings.ocr_pdf_max_pages == 3
    assert settings.ocr_worker_max_jobs == 1


def test_ocr_worker_max_jobs_is_capped() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            environment="test", database_url="postgresql://u:p@127.0.0.1:5433/x",
            redis_url="redis://127.0.0.1:6380/0", minio_endpoint="127.0.0.1:9002",
            minio_access_key="k", minio_secret_key="s", minio_bucket="b",
            jwt_secret="x" * 32, ocr_worker_max_jobs=5,
        )
```

- [ ] **Step 2: 实现**

`config.py` 的 OCR 段后追加：

```python
    # ---------- OCR 流水线（S3-1）----------
    # 协作式 deadline 必须小于 ARQ 的 job_timeout=180，留出写失败状态的余量
    ocr_pipeline_deadline_seconds: int = 165
    ocr_pdf_max_pages: int = 3                 # 任务卡"限前 3 页"
    # PaddleOCR 模型非线程安全（AGENTS §5）；1 是默认，2 只用于 IO/推理重叠对照
    ocr_worker_max_jobs: int = 1
```

```python
    @field_validator("ocr_worker_max_jobs")
    @classmethod
    def _check_worker_jobs(cls, value: int) -> int:
        if not 1 <= value <= 2:
            raise ValueError("OCR_WORKER_MAX_JOBS 只能是 1 或 2（模型非线程安全）")
        return value
```

`worker.py`：

```python
    max_jobs = get_settings().ocr_worker_max_jobs
```

`.env.example` 追加（附中文注释）：

```dotenv
# ---- OCR 流水线（S3-1）----
OCR_PIPELINE_DEADLINE_SECONDS=165
OCR_PDF_MAX_PAGES=3
OCR_WORKER_MAX_JOBS=1
```

- [ ] **Step 3: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_config.py
../.venv/Scripts/python.exe -c "from app.tasks.worker import WorkerSettings; print(WorkerSettings.max_jobs, WorkerSettings.job_timeout)"
```

预期输出 `1 180`。

- [ ] **Step 4: 提交**

```bash
git add backend/app/core/config.py backend/app/tasks/worker.py backend/.env.example backend/tests/test_config.py
git commit -m "feat(s3-1): 流水线 deadline/PDF 页数/worker 并发配置"
```

---

### Task 6: `services/ocr_pipeline.py` 核心编排

**Files:**
- Create: `backend/app/services/ocr_pipeline.py`
- Modify: `backend/app/tasks/ocr_task.py`
- Test: `backend/tests/test_ocr_pipeline.py`（新）

**Interfaces:**
- Produces（后续 Task 依赖，签名固定）：
  ```python
  PIPELINE_STAGES: tuple[str, ...] = ("preprocess", "detect", "recognize", "extract")
  TEMP_PREFIX = "ocr-pipeline-"
  NEED_REVIEW_THRESHOLD = 0.85

  @dataclass(frozen=True)
  class MergedField:
      field: ExtractedField
      page_no: int

  @dataclass
  class PipelineDeps:
      engine: object | None = None
      storage: object | None = None
      prepare_pages: Callable[..., list[PreparedPage]] | None = None
      templates: dict | None = None
      registry: object | None = None
      clock: Callable[[], float] = time.monotonic

  def merge_page_fields(results: list[tuple[int, ExtractionResult]]) -> tuple[dict[str, MergedField], list[str]]
  def invoice_values(merged: dict[str, MergedField], *, ticket_type: str, template_id: int | None) -> dict
  def line_rows(pages: list, *, invoice_id: int, job_id: int) -> list[dict]
  def field_rows(merged: dict[str, MergedField], *, invoice_id: int, rule_ids: dict[str, int],
                 failed_fields: set[str]) -> list[dict]
  def run_pipeline(db: Session, job: OcrJob, *, deps: PipelineDeps | None = None) -> None
  ```
- Consumes：`job_service.AbortAttempt`、`storage.get_bytes`、`intake.prepare_pages`、`extractor.rules_from_specs`、
  `layout.analyze_layout/assign_regions/load_templates`、`template_registry.registry`、`validators.run_validators`

#### 6.1 纯函数（先测后写）

- [ ] **Step 1: 写失败测试（`tests/test_ocr_pipeline.py` 单元区，默认 `pytest` 跑）**

```python
"""OCR 流水线（S3-1）：纯函数单测 + 临时库集成（假 engine/storage/intake，不加载模型）。"""
from __future__ import annotations

import pytest

from app.ocr.types import ExtractedField, ExtractionResult
from app.services.ocr_pipeline import (
    MergedField,
    field_rows,
    invoice_values,
    line_rows,
    merge_page_fields,
)


def _field(name: str, value: str, *, page: int = 1, confidence: float = 0.9,
           candidates: list[str] | None = None) -> ExtractedField:
    return ExtractedField(name=name, value=value, raw_text=f"{name}:{value}", source_line_no=3,
                          confidence=confidence, rule_id=f"{name}:text", bbox=(1, 2, 3, 4),
                          candidates=candidates or [])


def test_merge_page_fields_first_page_wins_and_records_conflict() -> None:
    page1 = ExtractionResult(fields={"total_amount": _field("total_amount", "100.00")}, ticket_type="vat_digital")
    page2 = ExtractionResult(fields={"total_amount": _field("total_amount", "200.00"),
                                     "seller_name": _field("seller_name", "某某公司")},
                             ticket_type="vat_digital")
    merged, warnings = merge_page_fields([(1, page1), (2, page2)])

    assert merged["total_amount"].field.value == "100.00"      # 首页优先，不覆盖
    assert merged["total_amount"].page_no == 1
    assert merged["seller_name"].page_no == 2                  # 后续页只补空
    assert "page_conflict:total_amount" in warnings


def test_invoice_values_converts_money_and_date_with_decimal() -> None:
    merged = {
        "total_amount": MergedField(_field("total_amount", "1234.56"), 1),
        "invoice_date": MergedField(_field("invoice_date", "2026-03-01"), 1),
        "invoice_no": MergedField(_field("invoice_no", "12345678"), 1),
    }
    values = invoice_values(merged, ticket_type="vat_digital", template_id=7)
    from decimal import Decimal
    from datetime import date

    assert values["total_amount"] == Decimal("1234.56")
    assert values["invoice_date"] == date(2026, 3, 1)
    assert values["invoice_no"] == "12345678"
    assert values["invoice_type"] == "vat_digital"
    assert values["template_id"] == 7
    assert "ocr_status" not in values            # 状态单独写，避免唯一索引冲突时被一起回滚
    assert "amount" not in values                # 缺失字段不写 None


def test_line_rows_use_four_point_box_and_page_no() -> None:
    class _Line:
        line_no = 0
        text = "价税合计"
        score = 0.91234
        box = [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]]

    class _Page:
        page_no = 2
        lines = [_Line()]
        blocks = {0: "b2-1"}

    rows = line_rows([_Page()], invoice_id=5, job_id=9)
    assert rows[0]["invoice_id"] == 5 and rows[0]["job_id"] == 9 and rows[0]["page_no"] == 2
    assert rows[0]["score"] == pytest.approx(0.912)
    assert rows[0]["box_json"][0] == [1.0, 2.0]      # 四点原样
    assert rows[0]["block_id"] == "b2-1"


def test_field_rows_flags_conflict_and_need_review() -> None:
    merged = {
        "total_amount": MergedField(_field("total_amount", "100.00", confidence=0.70,
                                           candidates=["100.00", "200.00"]), 1),
        "buyer_name": MergedField(_field("buyer_name", "张三", confidence=0.95), 1),
    }
    rows = field_rows(merged, invoice_id=5, rule_ids={"total_amount": 11}, failed_fields={"buyer_name"})
    by_name = {row["field_name"]: row for row in rows}
    assert by_name["total_amount"]["conflict"] is True
    assert by_name["total_amount"]["conflict_value"] == "200.00"
    assert by_name["total_amount"]["need_review"] is True      # confidence < 0.85
    assert by_name["total_amount"]["rule_id"] == 11
    assert by_name["buyer_name"]["need_review"] is True        # 校验失败也置 need_review
    assert by_name["buyer_name"]["source"] == "ocr"
    assert by_name["buyer_name"]["source_page_no"] == 1
```

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_pipeline.py
```

预期 `ModuleNotFoundError: app.services.ocr_pipeline`。

- [ ] **Step 3: 实现这四个纯函数**（放 `ocr_pipeline.py` 前部，无 DB 依赖）

```python
NEED_REVIEW_THRESHOLD = 0.85


def merge_page_fields(results: list[tuple[int, ExtractionResult]]
                      ) -> tuple[dict[str, MergedField], list[str]]:
    merged: dict[str, MergedField] = {}
    warnings: list[str] = []
    for page_no, result in sorted(results, key=lambda item: item[0]):
        for name, field in result.fields.items():
            existing = merged.get(name)
            if existing is None:
                merged[name] = MergedField(field=field, page_no=page_no)
            elif existing.field.value != field.value:
                warnings.append(f"page_conflict:{name}")
    return merged, warnings


_MONEY_COLUMNS = ("amount", "tax_amount", "total_amount")
_TEXT_COLUMNS = ("invoice_code", "invoice_no", "buyer_name", "buyer_tax_no",
                 "seller_name", "seller_tax_no", "check_code")


def invoice_values(merged: dict[str, MergedField], *, ticket_type: str,
                   template_id: int | None) -> dict[str, object]:
    values: dict[str, object] = {"invoice_type": ticket_type, "template_id": template_id}
    for name in _MONEY_COLUMNS:
        item = merged.get(name)
        amount = parse_money(item.field.value) if item is not None else None
        if amount is not None:
            values[name] = amount
    date_item = merged.get("invoice_date")
    parsed_date = parse_date(date_item.field.value) if date_item is not None else None
    if parsed_date is not None:
        values["invoice_date"] = parsed_date
    for name in _TEXT_COLUMNS:
        item = merged.get(name)
        if item is not None and item.field.value:
            values[name] = item.field.value
    return values


def line_rows(pages, *, invoice_id: int, job_id: int) -> list[dict]:
    rows: list[dict] = []
    for page in pages:
        for line in page.lines:
            rows.append({
                "invoice_id": invoice_id,
                "job_id": job_id,
                "page_no": page.page_no,
                "line_no": line.line_no,
                "text": line.text,
                "score": Decimal(str(round(float(line.score), 3))),
                "box_json": [[float(point[0]), float(point[1])] for point in line.box],
                "block_id": page.blocks.get(line.line_no),
            })
    return rows


def field_rows(merged: dict[str, MergedField], *, invoice_id: int,
               rule_ids: dict[str, int], failed_fields: set[str]) -> list[dict]:
    rows: list[dict] = []
    for name, item in merged.items():
        field = item.field
        conflicts = len(field.candidates) > 1
        rows.append({
            "invoice_id": invoice_id,
            "field_name": name,
            "field_value": field.value,
            "normalized_value": field.value,
            "raw_text": field.raw_text,
            "confidence": Decimal(str(round(float(field.confidence), 3))),
            "bbox_json": list(field.bbox) if field.bbox is not None else None,
            "source": FieldSource.OCR.value,
            "source_page_no": item.page_no,
            "source_line_no": field.source_line_no,
            "rule_id": rule_ids.get(name),
            "need_review": float(field.confidence) < NEED_REVIEW_THRESHOLD or name in failed_fields,
            "conflict": conflicts,
            "conflict_value": field.candidates[1] if conflicts else None,
        })
    return rows
```

`parse_money` / `parse_date` 从 `app.ocr.validators` 导入（`parse_date` 已存在）。

- [ ] **Step 4: 验证纯函数**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_pipeline.py
```

#### 6.2 集成：`run_pipeline`（TDD，`-m integration`）

- [ ] **Step 5: 写集成测试**（`tests/test_ocr_pipeline.py` 集成区；夹具照 `test_jobs.py` 的临时库写法）

```python
# ---- 假实现：不加载模型、不连 MinIO ----
class FakeEngine:
    def __init__(self, pages: dict[int, list[OcrLine]], *, error: Exception | None = None,
                 on_call=None) -> None:
        self.pages = pages
        self.error = error
        self.on_call = on_call
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        if self.on_call is not None:
            self.on_call(self.calls)
        if self.error is not None:
            raise self.error
        # 用 calls 当页码索引；测试里每页一张图
        from app.ocr.types import OcrResult
        lines = self.pages.get(self.calls - 1, [])
        return OcrResult(lines=lines, image_size=(1000, 600), elapsed_ms=10.0)


class FakeStorage:
    def __init__(self, payload: bytes = b"fake-image", *, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    def get_bytes(self, key, *, max_bytes):
        if self.error is not None:
            raise self.error
        return self.payload


def _fake_pages(count: int = 1, *, lines=None):
    from app.ocr.intake import PreparedPage
    import numpy as np
    return [PreparedPage(page_no=index + 1, image=np.zeros((600, 1000, 3), dtype=np.uint8),
                         kind="pdf", quality_flag=None) for index in range(count)]


def _deps(engine, storage=None, pages=1, **overrides):
    from app.services.ocr_pipeline import PipelineDeps
    return PipelineDeps(
        engine=engine,
        storage=storage or FakeStorage(),
        prepare_pages=lambda *args, **kwargs: _fake_pages(pages),
        templates=None,            # 用真实 YAML 模板（只读文件，不加载模型）
        registry=overrides.get("registry"),
        clock=overrides.get("clock", __import__("time").monotonic),
    )
```

需要覆盖的用例（每条都是独立函数，断言点写清楚）：

| 用例 | 断言 |
|---|---|
| `test_run_pipeline_happy_path_writes_rows` | `ocr_lines` 行数=假引擎行数；`invoice_fields` 含模板键；`invoices.ocr_status='succeeded'`、`invoice_type` 已写、`template_id` 非空；`ocr_jobs.metrics["stages"]` 四段齐全、`elapsed_ms` 非空（detect 允许 None）；`ocr_jobs.stage='extract'` |
| `test_run_pipeline_multipage_merges_pages` | 两页各 1 行文本 → `ocr_lines.page_no` 为 {1,2}；字段只来自首页；`metrics["pages_ocr"]==2` |
| `test_run_pipeline_empty_ocr_succeeds` | 0 行 → 作业 succeeded、`metrics["counts"]["lines"]==0`、`invoice_fields` 为空 |
| `test_run_pipeline_cipher_file_aborts_for_s4` | `is_cipher=True` → 抛 `ServiceUnavailableError`；`job_service.execute_job_attempt` 外层得到 failed，`error` 含"S4" |
| `test_run_pipeline_missing_object_is_retryable` | storage 抛 `ServiceUnavailableError` → `job_service` 回 `pending` 并抛 `RetryLater` |
| `test_run_pipeline_contract_error_not_retryable` | engine 抛 `OcrContractError` → 终态 failed，`error` 以 `OcrContractError` 开头 |
| `test_run_pipeline_deadline_exceeded` | `clock` 第 2 次调用返回 +1000s → 抛 `TimeoutError`，可重试 |
| `test_run_pipeline_manual_field_kept` | 预置 `invoice_fields(source='manual', field_name='buyer_name')` → 跑完仍是人工值 |
| `test_run_pipeline_duplicate_invoice_no_is_warning` | 预置另一张票同 `(invoice_code, invoice_no)` → 作业仍 succeeded、`metrics["duplicate_invoice_no"] is True`、本票 `invoice_no` 未写 |
| `test_run_pipeline_temp_dir_cleaned` | 跑完 `pathlib.Path(tempfile.gettempdir()).glob("ocr-pipeline-*")` 为空 |
| `test_run_pipeline_superseded_when_status_changed` | handler 执行中把 job 置 failed（第二连接）→ outcome `superseded`、业务表零写入 |
| `test_run_pipeline_sets_started_at_and_running_visible` | 假引擎在 recognize 时用第二连接读 `invoices.ocr_status=='running'` |

- [ ] **Step 6: 确认失败**：`../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_ocr_pipeline.py`

- [ ] **Step 7: 实现 `run_pipeline`**（结构如下，逐步补齐；每个阶段结束调用 `_flush_progress`）

```python
def run_pipeline(db: Session, job: OcrJob, *, deps: PipelineDeps | None = None) -> None:
    deps = deps or PipelineDeps()
    settings = get_settings()
    engine = deps.engine or get_engine(OcrOptions.from_settings(settings))
    storage = deps.storage or get_storage()
    prepare = deps.prepare_pages or prepare_pages
    clock = deps.clock
    deadline = clock() + settings.ocr_pipeline_deadline_seconds
    metrics = _new_metrics(attempt=job.retry_count)
    stage = OcrStage.PREPROCESS.value

    try:
        if job.status != OcrStatus.RUNNING.value:
            raise AbortAttempt("作业不在 running 状态")

        invoice, file = _load_invoice(db, job)
        _mark_invoice_running(db, invoice)

        started = clock()
        source = storage.get_bytes(file.storage_key, max_bytes=settings.max_upload_mb * 1024 * 1024)
        metrics["timings_ms"]["download"] = _ms(clock() - started)

        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
            path = Path(tmp) / "source.bin"
            path.write_bytes(source)
            started = clock()
            prepared = prepare(path, max_pages=settings.ocr_pdf_max_pages)
            metrics["timings_ms"]["intake"] = _ms(clock() - started)
        # 注：图像已在内存，临时目录此前已清理

        metrics["pages_total"] = len(prepared)
        _complete(metrics, OcrStage.PREPROCESS.value, metrics["timings_ms"]["intake"])
        _flush_progress(db, job, metrics, OcrStage.PREPROCESS.value)
        _check_deadline(deadline, clock)

        # 逐页识别
        pages: list[RecognizedPage] = []
        ocr_started = clock()
        stage = OcrStage.RECOGNIZE.value
        for page in prepared:
            _check_deadline(deadline, clock)
            result = engine.recognize(page.image)
            pages.append(RecognizedPage(page_no=page.page_no, lines=result.lines,
                                        image_size=result.image_size, prepared=page))
        ocr_ms = _ms(clock() - ocr_started)
        metrics["timings_ms"]["ocr"] = ocr_ms
        metrics["pages_ocr"] = len(pages)
        metrics["counts"]["lines"] = sum(len(page.lines) for page in pages)
        _complete(metrics, OcrStage.DETECT.value, None,
                  note="DBNet 与 SVTR 在同一次 PaddleOCR 调用内完成，无法单独计时")
        _complete(metrics, OcrStage.RECOGNIZE.value, ocr_ms)
        _flush_progress(db, job, metrics, OcrStage.RECOGNIZE.value)
        _check_deadline(deadline, clock)

        templates = deps.templates if deps.templates is not None else load_templates()
        classification = _classify(pages, templates)
        rules, rules_source, template_id = _resolve_rules(db, deps, classification.ticket_type)
        metrics["rules_source"] = rules_source
        metrics["template_id"] = template_id

        layout_started = clock()
        for page in pages:
            page.assignment = assign_regions(page.lines, page.image_size, classification, templates)
            page.blocks = {line_no: f"b{page.page_no}-{block.block_id}"
                           for block in page.assignment.blocks for line_no in block.line_nos}
        metrics["timings_ms"]["layout"] = _ms(clock() - layout_started)

        extract_started = clock()
        results = []
        for page in pages:
            layout = LayoutResult(classification=classification, regions=page.assignment)
            results.append((page.page_no, extract_fields(page.lines, page.image_size,
                                                         classification.ticket_type, layout, rules)))
        merged, page_warnings = merge_page_fields(results)
        metrics["warnings"].extend(page_warnings)
        failed = run_validators({name: item.field for name, item in merged.items()},
                                classification.ticket_type).failed_fields()
        metrics["counts"]["fields"] = len(merged)
        metrics["counts"]["missing_required"] = sorted(
            {name for _, result in results for name in result.missing_required})
        metrics["counts"]["conflicts"] = sum(1 for _, result in results for _ in result.conflicts)
        metrics["timings_ms"]["extract"] = _ms(clock() - extract_started)

        stage = OcrStage.EXTRACT.value
        persist_started = clock()
        _persist(db, invoice=invoice, job=job, pages=pages, merged=merged, failed_fields=failed,
                 classification=classification, template_id=template_id, metrics=metrics,
                 quality=prepared[0].quality_flag if prepared else None,
                 needs_retake=bool(prepared and prepared[0].needs_retake),
                 retake_hints=prepared[0].retake_hints if prepared else [],
                 settings=settings)
        metrics["timings_ms"]["persist"] = _ms(clock() - persist_started)
        _complete(metrics, OcrStage.EXTRACT.value, metrics["timings_ms"]["extract"])
        metrics["timings_ms"]["total"] = _ms(sum(
            metrics["timings_ms"][key] for key in ("download", "intake", "ocr", "layout", "extract", "persist")))
        metrics["warnings"] = _cap_warnings(metrics["warnings"])
        job.stage = OcrStage.EXTRACT.value
        job.metrics = metrics          # 不 commit：与 job_service 的 succeeded 一起提交
    except Exception as exc:
        _record_failure(db, job, stage, metrics, exc)
        raise
```

同文件补齐私有件（签名固定）：

```python
@dataclass
class RecognizedPage:
    page_no: int
    lines: list[OcrLine]
    image_size: tuple[int, int]
    prepared: PreparedPage
    assignment: RegionAssignment | None = None
    blocks: dict[int, str] = field(default_factory=dict)


def _new_metrics(*, attempt: int) -> dict:
    return {
        "version": 1, "attempt": attempt, "pages_total": 0, "pages_ocr": 0,
        "stages": [], "timings_ms": {"download": 0, "intake": 0, "ocr": 0, "layout": 0,
                                     "extract": 0, "persist": 0, "total": 0},
        "counts": {"lines": 0, "fields": 0, "missing_required": [], "conflicts": 0},
        "engine": {}, "rules_source": "", "template_id": None,
        "quality": {"flag": None, "needs_retake": False, "retake_hints": [], "reasons": []},
        "warnings": [], "duplicate_invoice_no": False,
    }


def _complete(metrics, stage: str, elapsed_ms: float | None, note: str | None = None) -> None:
    entry = {"stage": stage, "status": "succeeded",
             "elapsed_ms": None if elapsed_ms is None else int(round(elapsed_ms))}
    if note:
        entry["note"] = note
    metrics["stages"].append(entry)


def _fail_stage(metrics, stage: str) -> None:
    metrics["stages"].append({"stage": stage, "status": "failed", "elapsed_ms": None})


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def _check_deadline(deadline: float, clock) -> None:
    if clock() > deadline:
        raise TimeoutError("OCR 流水线超过时间预算")


def _cap_warnings(warnings: list[str], limit: int = 20) -> list[str]:
    return list(dict.fromkeys(warnings))[:limit]
```

`_load_invoice` / `_mark_invoice_running` / `_persist` / `_record_failure` / `_resolve_rules` / `_classify` 的实现要点：

- `_load_invoice`：`invoice = db.get(Invoice, job.invoice_id)`，不存在或 `status=='void'` → 抛 `ValidationFailedError("票据不存在或已作废")`（永久失败）；`file = db.get(InvoiceFile, invoice.file_id)` 缺失 → 同上；`is_cipher` → `ServiceUnavailableError("加密影像解密尚未接入（S4）")`。
- `_mark_invoice_running`：`db.execute(update(Invoice).where(Invoice.id==invoice.id, Invoice.ocr_status.in_(("pending","running"))).values(ocr_status="running"))` + `db.commit()` + `db.refresh(invoice)`。
- `_classify`：对 `pages` 逐页 `classify_ticket(page.lines, page.image_size, templates)`，取第一个 `is_known` 的页；全未知 → 返回首页的 `Classification("unknown", ...)`。
- `_resolve_rules`：`registry = deps.registry or template_registry.registry`；`snapshots = registry.active_templates(db)`；
  命中 → `(rules_from_specs(snapshot.field_specs_json), "db", snapshot.id)`；未命中/空 → `(load_field_rules(), "yaml", None)` 并 `metrics["warnings"].append("rules_yaml_fallback:<type>")`。
- `_persist`：
  ```python
  db.execute(delete(OcrLine).where(OcrLine.invoice_id == invoice.id))
  db.execute(insert(OcrLine), line_rows(pages, invoice_id=invoice.id, job_id=job.id))
  db.execute(delete(InvoiceField).where(InvoiceField.invoice_id == invoice.id,
                                        InvoiceField.source != FieldSource.MANUAL.value,
                                        InvoiceField.confirmed.is_(False)))
  db.execute(insert(InvoiceField), field_rows(merged, invoice_id=invoice.id,
                                              rule_ids=_rule_ids(db, template_id, merged.keys()),
                                              failed_fields=failed_fields))
  values = invoice_values(merged, ticket_type=classification.ticket_type, template_id=template_id)
  quality = {"quality_flag": quality} if quality in ("ok", "low") else {}
  final = {"ocr_status": OcrStatus.SUCCEEDED.value, **quality}
  try:
      with db.begin_nested():
          db.execute(update(Invoice).where(Invoice.id == invoice.id,
                                           Invoice.ocr_status.in_(("pending", "running")))
                     .values(**values, **final))
  except IntegrityError:
      metrics["duplicate_invoice_no"] = True
      metrics["warnings"].append("duplicate_invoice_no")
      db.execute(update(Invoice).where(Invoice.id == invoice.id,
                                       Invoice.ocr_status.in_(("pending", "running")))
                 .values(invoice_type=classification.ticket_type, template_id=template_id, **final))
  metrics["quality"] = {"flag": quality.get("quality_flag"), "needs_retake": needs_retake,
                        "retake_hints": retake_hints, "reasons": []}
  ```
  `_rule_ids(db, template_id, names)`：`template_id is None` → `{}`；否则
  `select(FieldRule.field_name, FieldRule.id).where(FieldRule.template_id==template_id, FieldRule.field_name.in_(names), FieldRule.active.is_(True))` → dict。
- `_record_failure`：
  ```python
  db.rollback()
  fresh = db.get(OcrJob, job.id)
  if fresh is None:
      return
  if fresh.status == OcrStatus.RUNNING.value:
      _fail_stage(metrics, stage)
      fresh.stage = stage
      fresh.metrics = dict(metrics, warnings=_cap_warnings(metrics["warnings"]))
      db.commit()
  ```
- `_flush_progress(db, job, metrics, stage, *, commit=True)`：`job.stage = stage; job.metrics = dict(metrics)`；commit 时 `db.commit()`。

- [ ] **Step 8: 接线 `ocr_task`**

`tasks/ocr_task.py`：

```python
def _run_pipeline(db, job) -> None:
    """S3-1：真实流水线（下载 → 渲染/预处理 → 检测识别 → 票种 → 抽取 → 校验 → 落库）。"""
    from app.services.ocr_pipeline import run_pipeline  # 延迟导入：避免 API 进程加载 cv2

    run_pipeline(db, job)
```

- [ ] **Step 9: 跑测试**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_pipeline.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_ocr_pipeline.py tests/test_jobs.py
../.venv/Scripts/python.exe -m pytest -q          # 全量不回归
```

- [ ] **Step 10: 提交**

```bash
git add backend/app/services/ocr_pipeline.py backend/app/tasks/ocr_task.py backend/tests/test_ocr_pipeline.py
git commit -m "feat(s3-1): OCR 流水线编排（下载→识别→抽取→落库）接入 ARQ"
```

---

### Task 7: `GET /invoices/{id}/ocr-status`

**Files:**
- Create: `backend/app/services/ocr_status_service.py`
- Modify: `backend/app/schemas/invoice.py`
- Modify: `backend/app/api/v1/invoices.py`
- Modify: `docs/API.md`（§2 该接口：实现标记改 `S3-1 已实现`；补 detect 不单独计时的说明）
- Test: `backend/tests/test_ocr_status.py`（新）

**Interfaces:**
- Produces: `ocr_status_service.get_ocr_status(db, user, invoice_id: int) -> OcrStatusData`；
  `ocr_status_service.compute_progress(stages: list[dict], ocr_status: str) -> int`；
  `ocr_status_service.build_stages(job, metrics) -> list[OcrStageOut]`

- [ ] **Step 1: 写失败测试**

单元（纯函数，不连 DB）：

```python
from app.services.ocr_status_service import STAGE_WEIGHTS, compute_progress


def test_compute_progress_accumulates_completed_stage_weights() -> None:
    stages = [{"stage": "preprocess", "status": "succeeded"}]
    assert compute_progress(stages, "running") == 10
    stages.append({"stage": "detect", "status": "succeeded"})
    stages.append({"stage": "recognize", "status": "succeeded"})
    assert compute_progress(stages, "running") == 80


def test_compute_progress_succeeded_is_100() -> None:
    assert compute_progress([], "succeeded") == 100


def test_compute_progress_failed_keeps_completed_weight() -> None:
    stages = [{"stage": "preprocess", "status": "succeeded"},
              {"stage": "recognize", "status": "failed"}]
    assert compute_progress(stages, "failed") == 10


def test_stage_weights_match_api_contract() -> None:
    assert STAGE_WEIGHTS == {"preprocess": 10, "detect": 30, "recognize": 40, "extract": 20}
```

集成（临时库 HTTP，夹具照 `tests/test_invoices.py` 的 `temporary_database` + `dependency_overrides[get_db]` 写法；
模块级 `pytestmark = pytest.mark.integration`，临时库前缀 `study_ocr_ocrst_`）：

```python
@pytest.fixture()
def actors(session_factory, clean_db) -> dict[str, User]:
    with session_factory() as db:
        users = {
            "alice": User(username="alice", real_name="A", role="submitter", status="active"),
            "bob": User(username="bob", real_name="B", role="submitter", status="active"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(user_id=user.id, role=user.role,
                            token_type=TokenType.ACCESS, ttl_seconds=60)
    return {"Authorization": f"Bearer {token}"}


def _make_invoice_with_job(session_factory, uploader_id: int, *, metrics: dict | None = None,
                           ocr_status: str = "running", started: bool = True) -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin")
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=uploader_id, file_id=file.id)
        db.execute(sa_text("update invoices set ocr_status = :status, invoice_type = 'vat_digital'"
                           " where id = :id"), {"status": ocr_status, "id": invoice.id})
        job = OcrJob(
            invoice_id=invoice.id, stage="extract", status=ocr_status, engine="paddleocr",
            engine_version="2.7.3", retry_count=1, elapsed_ms=1820,
            started_at=datetime.now(timezone.utc) if started else None,
            metrics=metrics,
        )
        db.add(job)
        db.commit()
        return invoice.id


def test_ocr_status_owner_reads_stages_and_progress(client, session_factory, actors) -> None:
    metrics = {"version": 1, "stages": [
        {"stage": "preprocess", "status": "succeeded", "elapsed_ms": 80},
        {"stage": "detect", "status": "succeeded", "elapsed_ms": None},
        {"stage": "recognize", "status": "succeeded", "elapsed_ms": 1400},
    ]}
    invoice_id = _make_invoice_with_job(session_factory, actors["alice"].id, metrics=metrics)

    response = client.get(f"{BASE}/{invoice_id}/ocr-status", headers=_auth(actors["alice"]))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["invoice_id"] == invoice_id
    assert data["ocr_status"] == "running"
    assert data["progress"] == 80
    assert [stage["stage"] for stage in data["stages"]] == ["preprocess", "detect", "recognize"]
    assert data["stages"][1]["elapsed_ms"] is None
    assert data["stages"][0]["engine_version"] == "2.7.3"
    assert data["started_at"] is not None and data["finished_at"] is None


def test_ocr_status_reviewer_can_read_others(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_job(session_factory, actors["alice"].id)
    response = client.get(f"{BASE}/{invoice_id}/ocr-status", headers=_auth(actors["rita"]))
    assert response.status_code == 200


def test_ocr_status_other_submitter_gets_404(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_job(session_factory, actors["alice"].id)
    assert client.get(f"{BASE}/{invoice_id}/ocr-status",
                      headers=_auth(actors["bob"])).status_code == 404


def test_ocr_status_admin_gets_404(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_job(session_factory, actors["alice"].id)
    assert client.get(f"{BASE}/{invoice_id}/ocr-status",
                      headers=_auth(actors["ada"])).status_code == 404


def test_ocr_status_without_job_returns_empty_structure(client, session_factory, actors) -> None:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=actors["alice"].id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin")
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=actors["alice"].id, file_id=file.id)
        invoice_id = invoice.id
        db.commit()
    data = client.get(f"{BASE}/{invoice_id}/ocr-status", headers=_auth(actors["alice"])).json()["data"]
    assert data["stages"] == [] and data["progress"] == 0
    assert data["started_at"] is None and data["finished_at"] is None
```

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_status.py
```

- [ ] **Step 3: 实现 service**

```python
"""`GET /invoices/{id}/ocr-status` 读模型（S3-1）：进度权重与阶段明细。

`stages` 来自 `ocr_jobs.metrics.stages`（真数据）；没有 metrics 的历史作业退化为单条。
"""
STAGE_WEIGHTS = {"preprocess": 10, "detect": 30, "recognize": 40, "extract": 20}


def compute_progress(stages: list[dict], ocr_status: str) -> int:
    if ocr_status == OcrStatus.SUCCEEDED.value:
        return 100
    done = sum(STAGE_WEIGHTS.get(item.get("stage", ""), 0)
               for item in stages if item.get("status") == "succeeded")
    return min(done, 100)


def get_ocr_status(db: Session, user: User, invoice_id: int) -> OcrStatusData:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    job = db.scalars(
        select(OcrJob).where(OcrJob.invoice_id == invoice_id)
        .order_by(OcrJob.created_at.desc(), OcrJob.id.desc()).limit(1)
    ).first()
    metrics = dict(job.metrics or {}) if job is not None else {}
    stages = build_stages(job, metrics)
    return OcrStatusData(
        invoice_id=invoice.id,
        ocr_status=invoice.ocr_status,
        progress=compute_progress([stage.model_dump() for stage in stages], invoice.ocr_status),
        stages=stages,
        started_at=job.started_at if job is not None else None,
        finished_at=job.finished_at if job is not None else None,
    )
```

`build_stages(job, metrics)`：metrics 有 `stages` → 逐条转 `OcrStageOut(stage, status, elapsed_ms, note,
engine=job.engine, engine_version=job.engine_version, retry_count=job.retry_count,
error=job.error if item.status=='failed' else None)`；无 → 单条 `job.stage/job.status`。

- [ ] **Step 4: schema + 路由**

`schemas/invoice.py`：

```python
class OcrStageOut(BaseModel):
    stage: str
    status: str
    elapsed_ms: int | None = None
    note: str | None = None
    engine: str | None = None
    engine_version: str | None = None
    retry_count: int = 0
    error: str | None = None


class OcrStatusData(BaseModel):
    invoice_id: int
    ocr_status: str
    progress: int = Field(ge=0, le=100)
    stages: list[OcrStageOut] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
```

`api/v1/invoices.py`：

```python
@router.get("/{invoice_id}/ocr-status", response_model=Envelope[OcrStatusData],
            summary="识别进度（轮询）")
def get_invoice_ocr_status(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(ocr_status_service.get_ocr_status(db, user, invoice_id))
```

（放在 `get_invoice` 之后即可，FastAPI 路由表不冲突。）

- [ ] **Step 5: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_status.py tests/test_api_docs.py
# 真接口（需后端起 + 登录 token）
curl -s localhost:8000/api/v1/invoices/1/ocr-status -H "Authorization: Bearer <token>"
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/ocr_status_service.py backend/app/schemas/invoice.py backend/app/api/v1/invoices.py backend/tests/test_ocr_status.py docs/API.md
git commit -m "feat(s3-1): 实现 GET /invoices/{id}/ocr-status（stages/progress/起止时间）"
```

---

### Task 8: 前端轮询切换

**Files:**
- Modify: `web/src/types/invoice.ts`
- Modify: `web/src/api/invoices.ts`
- Modify: `web/src/views/Upload.vue`

**Interfaces:**
- Produces: `OcrStage`/`OcrStatusData` 类型；`waitForOcr(invoiceId, onUpdate?: (status: OcrStatusData) => void, signal?, totalTimeoutMs?)`

- [ ] **Step 1: 加类型**

```ts
export interface OcrStage {
  stage: string
  status: OcrStatus
  elapsed_ms: number | null
  note?: string | null
  engine?: string | null
  engine_version?: string | null
  retry_count: number
  error: string | null
}

export interface OcrStatusData {
  invoice_id: number
  ocr_status: OcrStatus
  progress: number
  stages: OcrStage[]
  started_at: string | null
  finished_at: string | null
}
```

- [ ] **Step 2: 改 `waitForOcr`**

```ts
export async function waitForOcr(
  invoiceId: number,
  onUpdate?: (status: OcrStatusData) => void,
  signal?: AbortSignal,
  totalTimeoutMs: number = TOTAL_TIMEOUT_MS,
): Promise<PollOutcome> {
  const started = Date.now()
  let delay = START_DELAY_MS
  while (Date.now() - started < totalTimeoutMs) {
    await sleep(delay)
    if (signal?.aborted) return 'aborted'
    try {
      const envelope = await get<OcrStatusData>(`/invoices/${invoiceId}/ocr-status`, { silent: true })
      const status = unwrap(envelope)
      if (signal?.aborted) return 'aborted'
      onUpdate?.(status)
      if (TERMINAL_STATUSES.has(status.ocr_status)) return 'done'
    } catch {
      // 网络抖动/瞬时 5xx：继续重试，直到总超时
    }
    delay = Math.min(Math.round(delay * 1.5), MAX_DELAY_MS)
  }
  return 'timeout'
}
```

同时删掉不再使用的 `InvoiceDetail` 导入（typecheck 会提示）。

- [ ] **Step 3: 改 `Upload.vue`**

```ts
void waitForOcr(
  task.invoiceId,
  (status) => {
    task.ocrStatus = status.ocr_status
    task.jobStatus = status.ocr_status
    task.jobError = status.stages.find((stage) => stage.error)?.error ?? null
    task.message = `识别中… ${status.progress}%`
  },
  controller.signal,
).then((outcome) => { /* 原逻辑不变 */ })
```

并把文件头注释里"轮询用详情接口…（S3-1 换 ocr-status）"改成"轮询 `GET /invoices/{id}/ocr-status`"。

- [ ] **Step 4: 验证**

```bash
cd /e/BiSheXZY/study-OCR/web
npm run typecheck && npm run build
# 上传页冒烟（需后端 + worker + Vite 都在跑）
node ../scripts/ui_smoke.mjs "http://127.0.0.1:5173/upload" \
  --upload 'input[type=file]' 'E:\BiSheXZY\study-OCR\samples\synth_v1\vat_digital_landscape_001.jpg' \
  --expect 识别
```

- [ ] **Step 5: 提交**

```bash
git add web/src/types/invoice.ts web/src/api/invoices.ts web/src/views/Upload.vue
git commit -m "feat(s3-1): 上传页轮询改用 ocr-status，展示阶段进度"
```

---

### Task 9: 真实验收脚本 + 20 张实测 + 文档收口

**Files:**
- Create: `scripts/ocr_acceptance.py`
- Modify: `docs/PROGRESS.md`、`docs/DECISIONS.md`、`docs/HANDOFF.md`、`AGENTS.md`（§10.8 锚点）、`docs/API.md`、`docs/SCHEMA.md`（若 Task 1 有遗漏）

- [ ] **Step 1: 写验收脚本**

```python
"""S3-1 真实链路验收：登录 → 逐张上传 → 轮询 ocr-status → 统计成功率/P50/P95 → 可选清理。

用法：
    ../.venv/Scripts/python.exe scripts/ocr_acceptance.py --samples 20
    ../.venv/Scripts/python.exe scripts/ocr_acceptance.py --samples 20 --cleanup
前置：uvicorn :8000 与 arq worker 都在跑；samples/synth_v1 有 ≥20 张样例。
口径：单张耗时取 `GET /invoices/{id}` 的 `latest_job.elapsed_ms`（job_service 实测，不是脚本计时）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))          # 与 scripts/ocr_baseline.py 同样的做法

import httpx  # noqa: E402

TEMP_PREFIX = "ocr-pipeline-"
SAMPLE_DIR = ROOT / "samples" / "synth_v1"
RESULT_DIR = ROOT / "backend" / "eval" / "results"


def login(client: httpx.Client, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return response.json()["data"]["access_token"]


def upload(client: httpx.Client, token: str, path: Path) -> dict | None:
    with path.open("rb") as handle:
        response = client.post("/api/v1/invoices/upload", headers={"Authorization": f"Bearer {token}"},
                               files={"file": (path.name, handle, "image/jpeg")})
    if response.status_code == 409:
        return None                                  # 重复影像：跳过，不计入
    response.raise_for_status()
    return response.json()["data"]


def poll(client, token: str, invoice_id: int, *, timeout_s: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data = client.get(f"/api/v1/invoices/{invoice_id}/ocr-status",
                          headers={"Authorization": f"Bearer {token}"}).json()["data"]
        if data["ocr_status"] in {"succeeded", "failed"}:
            return data
        time.sleep(2)
    raise TimeoutError(f"票据 {invoice_id} 60s 未到终态")


def detail(client, token: str, invoice_id: int) -> dict:
    return client.get(f"/api/v1/invoices/{invoice_id}",
                      headers={"Authorization": f"Bearer {token}"}).json()["data"]


def cleanup(invoice_ids: list[int]) -> None:
    """按票据 id 删业务行并删除 MinIO 对象（不打印对象名）。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.services.storage import get_storage

    settings = get_settings()
    storage = get_storage()
    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        keys = db.execute(text("select storage_key from invoice_files where id in "
                               "(select file_id from invoices where id = any(:ids))"),
                          {"ids": invoice_ids}).scalars().all()
        db.execute(text("delete from ocr_lines where invoice_id = any(:ids)"), {"ids": invoice_ids})
        db.execute(text("delete from invoice_fields where invoice_id = any(:ids)"), {"ids": invoice_ids})
        db.execute(text("delete from ocr_jobs where invoice_id = any(:ids)"), {"ids": invoice_ids})
        db.execute(text("delete from invoices where id = any(:ids)"), {"ids": invoice_ids})
        db.execute(text("delete from invoice_files where storage_key = any(:keys)"), {"keys": keys})
        db.commit()
    for key in keys:
        storage.delete(key)
    print(f"已清理 {len(invoice_ids)} 张票据与 {len(keys)} 个对象")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password", required=True)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    samples = sorted(SAMPLE_DIR.glob("*.jpg"))[: args.samples]
    if len(samples) < args.samples:
        print(f"样例不足：需要 {args.samples} 张，实际 {len(samples)} 张")
        return 2

    rows: list[dict] = []
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        token = login(client, args.username, args.password)
        for path in samples:
            data = upload(client, token, path)
            if data is None:
                rows.append({"file": path.name, "skipped": True})
                continue
            status = poll(client, token, data["invoice_id"])
            elapsed = detail(client, token, data["invoice_id"])["latest_job"]["elapsed_ms"]
            rows.append({"file": path.name, "invoice_id": data["invoice_id"],
                         "ocr_status": status["ocr_status"], "elapsed_ms": elapsed,
                         "progress": status["progress"],
                         "error": next((s["error"] for s in status["stages"] if s["error"]), None)})

    done = [row for row in rows if not row.get("skipped")]
    ok = [row for row in done if row["ocr_status"] == "succeeded"]
    durations = [row["elapsed_ms"] for row in ok if row["elapsed_ms"] is not None]
    leftover = sorted(Path(tempfile.gettempdir()).glob(f"{TEMP_PREFIX}*"))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(done), "succeeded": len(ok), "failed": len(done) - len(ok),
        "success_rate": round(len(ok) / len(done), 3) if done else 0.0,
        "p50_ms": statistics.median(durations) if durations else None,
        "p95_ms": (statistics.quantiles(durations, n=20)[-1] if len(durations) >= 20
                   else max(durations) if durations else None),
        "temp_leftover": [str(path) for path in leftover],
        "rows": rows,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"ocr_acceptance_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"成功 {report['succeeded']}/{report['samples']}（{report['success_rate']:.1%}）"
          f" P50 {report['p50_ms']}ms P95 {report['p95_ms']}ms")
    for row in done:
        if row["ocr_status"] != "succeeded":
            print(f"  失败 {row['file']}: {row['error']}")
    if leftover:
        print(f"  ⚠ 临时目录残留：{leftover}")
    print(f"报告：{out}")

    if args.cleanup:
        cleanup([row["invoice_id"] for row in done])
    return 0 if len(ok) >= len(done) - 2 and not leftover else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 起三个终端跑真实验收**

```bash
# 终端 1
cd /e/BiSheXZY/study-OCR/backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
# 终端 2
cd /e/BiSheXZY/study-OCR/backend && ../.venv/Scripts/python.exe -m arq app.tasks.worker.WorkerSettings
# 终端 3
cd /e/BiSheXZY/study-OCR && .venv/Scripts/python.exe scripts/ocr_acceptance.py --samples 20
```

判定：成功 ≥18；P95 < 30s；临时目录残留 0；任取一条失败记录能读到 `error`。
跑完先 `--cleanup` 再勾 PROGRESS（验收数据不留库）。

- [ ] **Step 3: 全量回归**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
```

- [ ] **Step 4: 文档收口**

- `docs/PROGRESS.md`：S3-1 勾选 + 实测数字（成功率/P50/P95/失败原因）+ 遗留变化：
  关闭 #35（本卡不补重跑入口，改为 S3-8）、#36（规则来源已定）、#42（前端已换接口）；新增
  "`field_value` 暂存归一值，待 S3-4 补 `raw_value`"；
- `docs/DECISIONS.md`：D-055（一 run 一行 + 提交语义扩到 `invoices.ocr_status`）、
  D-056（新增 `metrics/started_at/finished_at`，推翻 S2-7 的"不加列"）、
  D-057（PDF 复用 PyMuPDF/前 3 页；规则 DB 优先 YAML 回退；worker 并发 1；协作式 deadline 而非硬杀）；
- `AGENTS.md` §10.8 追加一行："OCR 流水线：`services/ocr_pipeline.py`（`run_pipeline` + 依赖注入
  `PipelineDeps`；`job_service` 终态守卫；`metrics` 结构；临时目录 `ocr-pipeline-` 前缀）| S3-1 ✅"；
- `docs/HANDOFF.md`：一句话现状改"S3-1 已通，下一步 S3-2"，§7 换成 S3-2 的入口；
- `docs/API.md`：`ocr-status` 已实现标记 + detect 计时说明。

- [ ] **Step 5: 提交**

```bash
git add scripts/ocr_acceptance.py docs backend web
git commit -m "feat(s3-1): OCR Worker 串起全链路（识别→落库→ocr-status），附 20 张验收"
```

---

## 自检（写完计划后逐条核对 spec）

| spec 条目 | 覆盖 Task |
|---|---|
| §1 产出 1–13 | Task 2/3/4/5/6/7/8/9（`invoice_service` 详情不改，已在 spec §1 修正） |
| §2 三处裁决（加列 / 一 run 一行 / 换前端） | Task 1、Task 6、Task 8 |
| §3.1 八步数据流 | Task 6.2 `run_pipeline` |
| §3.2 metrics 结构 | Task 6.2 `_new_metrics/_complete` |
| §3.3 迁移三列 | Task 1 |
| §3.4 ocr-status 契约 | Task 7 |
| §3.5 落库规则（含唯一索引 savepoint、人工字段保护） | Task 6.2 `_persist` + 集成用例 |
| §3.6 提交语义 | Task 6.2 `_flush_progress` + Task 1 SCHEMA 措辞 |
| §3.7 错误/超时/幂等/僵尸 | Task 2（superseded）+ Task 6（deadline/`_record_failure`） |
| §3.8 worker 并发 | Task 5 |
| §3.9 前端 | Task 8 |
| §4 十处冲突 | Task 1（G1/G2/§3.6）、Task 3（PyMuPDF）、Task 4（规则来源）、Task 6（`field_value`/`rule_id`）、Task 2（D-056） |
| §5 测试清单 | Task 2/3/4/6/7 各自测试节 |
| §6 验收命令与指标 | Task 9 |
| §7 风险 | Task 9（seed 幂等、全量回归）+ Task 6（deadline） |
| §8 收口 | Task 9 Step 4 |

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 0004 迁移有问题 | `alembic downgrade 0003`（可空列，无数据丢失）；修模型/迁移重跑；**不改历史迁移** |
| DB 模板规格过期导致字段变差 | 跑 `scripts/seed_templates.py` 重新 seed（幂等）；或让 `_resolve_rules` 走 YAML 回退并记 `rules_source=yaml` |
| 真实链路 P95 超 30s | 先看 `metrics.timings_ms` 定位（ocr vs persist）；OCR 侧调 `OCR_DET_LIMIT_SIDE_LEN=1280`，持久化侧改批量 insert 大小；如实记录未达标 |
| 前端换接口后上传页异常 | 回退 `web/src/api/invoices.ts` 单文件（后端接口保留，详情接口未变） |
| worker 卡在 running | ARQ 超时 + `recover_stale_jobs` 兜底；`ocr-status` 的 `started_at` 可判断是否僵尸 |