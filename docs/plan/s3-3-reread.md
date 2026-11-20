# S3-3 局部二次识别实现计划（plan）

> 对应 spec：[`docs/spec/s3-3-reread.md`](../spec/s3-3-reread.md)（已确认）｜
> 契约：`docs/API.md` §2 `POST /invoices/{id}/reocr`（202）｜ `SCHEMA.md` §3.3/§3.4/§9.19b ｜ 状态：**已实现（2026-11-20）**，Task 1–8 全部执行并验证（见 `docs/PROGRESS.md` S3-3 行）
> 执行方式：Task 1→8 顺序；每个 Task 跑完验证命令再进下一个。

**目标**：实现契约里的异步 `reocr`（按字段裁剪+放大重识别），只更新 `raw_text/confidence`、不动值；
顺带修 S3-1 的 bbox 坐标偏差、补 `field_reread` 审计枚举（审计行仍留 S4-7）。

**架构**：`ocr/refine.py` 是裁剪原语（numpy/OpenCV，无 DB）；`services/reocr_service.py` 做校验、作业与写回；
ARQ `reocr_task` 复用 `job_service` 状态机；坐标映射与原图 bbox 修正放在 `ocr_pipeline`。

**技术栈**：Python 3.11 / numpy / OpenCV / SQLAlchemy 2.0 / Alembic / ARQ / Vue 3 + TS（无新增依赖）。

## Global Constraints

- 命令用 `.venv/Scripts/python.exe`；默认工作目录 `E:\BiSheXZY\study-OCR\backend`。
- **不改**已提交迁移；新枚举只走 `0005`。
- `field_value/normalized_value` **不得**被重识别修改；`manual/confirmed` 字段不得被触发。
- 金额 `Decimal`；置信度 `NUMERIC(4,3)` 一律 `round(x,3)`。
- 裁剪/坐标纯函数不连 DB；测试不加载模型、不连 MinIO/Redis（集成用临时库 + 假实现）。
- 文档同步：`SCHEMA`（审计枚举 + bbox 口径）、`API.md`（reocr 细节）、`.env.example`、`PROGRESS`、`DECISIONS`。
- 提交 Conventional Commits（`feat(s3-3): ...`）；**push 由用户执行**。

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/ocr/refine.py`（新） | 外扩/clamp/放大/单行识别/坐标映射（纯函数） |
| `backend/app/services/reocr_service.py`（新） | `submit_reocr` / `run_reocr` / `ReocrDeps` |
| `backend/app/schemas/invoice.py` | `ReocrRequest` / `ReocrData` |
| `backend/app/api/v1/invoices.py` | `POST /{id}/reocr`（202） |
| `backend/app/tasks/{ocr_task,worker,queue}.py` | `reocr_task` + `enqueue_reocr` |
| `backend/app/core/config.py` + `.env.example` | 4 个 `REREAD_*` 配置 |
| `backend/app/models/enums.py` + `alembic/versions/0005_*.py` | `AuditAction.FIELD_REREAD` |
| `backend/app/services/ocr_pipeline.py` | 字段 bbox 写原图坐标 |
| `backend/tests/{test_refine,test_reocr}.py` + 补 `test_ocr_pipeline`/`test_jobs`/`test_config` | 单元 + 集成 |
| `web/src/{api/invoices.ts,types/invoice.ts,views/InvoiceDetail.vue}` | 字段"重识别"按钮 |
| `scripts/reread_eval.py`（新） | 40 张离线提升率验收 |

---

### Task 1: 契约/枚举/迁移/配置

**Files:**
- Modify: `docs/SCHEMA.md`（§9.19b 动作清单 + §3.3 bbox 口径备注）
- Modify: `backend/app/models/enums.py`
- Create: `backend/alembic/versions/0005_audit_action_field_reread.py`
- Modify: `backend/app/core/config.py`、`backend/.env.example`
- Test: `backend/tests/test_config.py`、`backend/tests/test_models.py`

**Interfaces:**
- Produces: `AuditAction.FIELD_REREAD`；`Settings.reread_pad_min_px/reread_pad_ratio/reread_scale/reread_budget_seconds`

- [ ] **Step 1: SCHEMA 先行**

`docs/SCHEMA.md`：
1. §9.19b 行末追加 `field_reread`，并注明"S3-3 拍板补（`POST /invoices/{id}/reocr`；行仍留 S4-7 写）"；
2. §9.19 表 `action` 行的值列表同步；
3. §3.3 `bbox_json` 行补一句"**由识别图（校正图）四点坐标经 `to_original` 映射回原图**；
   `ocr_lines.box_json` 保持校正图坐标（S3-3/D-060）"。

`docs/API.md` §2 `reocr` 条目的"实现"改为 `S3-3 ✅`，备注补充（字段校验、只改 raw_text/confidence、
metrics 明细、1.5s 预算、空文本 skipped）。

- [ ] **Step 2: 枚举 + 迁移**

`models/enums.py::AuditAction` 增：

```python
    FIELD_REREAD = "field_reread"
```

`alembic/versions/0005_audit_action_field_reread.py`（照 0002 的写法，手写，**不 autogenerate**）：

```python
"""audit action: add field_reread

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20

S3-3（D-061）：`POST /invoices/{id}/reocr` 需要 audit_logs.action=field_reread。
本迁移只放开 CHECK，**不写审计行**——哈希链在 S4-7 实现，届时才允许 INSERT。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = (
    "upload", "confirm_field", "sign", "seal", "submit", "verify", "decrypt",
    "review", "revoke_cert", "login_failed", "logout", "forbidden",
)
_NEW_VALUES = (*_OLD_VALUES, "field_reread")


def _check_sql(values: Sequence[str]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"action IN ({allowed})"


def upgrade() -> None:
    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint("ck_audit_logs_action", "audit_logs", _check_sql(_NEW_VALUES))


def downgrade() -> None:
    bind = op.get_bind()
    used = bind.execute(
        sa.text("select count(*) from audit_logs where action = 'field_reread'")
    ).scalar_one()
    if used:
        raise RuntimeError("审计表中已有 field_reread 的行，禁止 downgrade（审计不可删）")
    op.drop_constraint("ck_audit_logs_action", "audit_logs", type_="check")
    op.create_check_constraint("ck_audit_logs_action", "audit_logs", _check_sql(_OLD_VALUES))
```

- [ ] **Step 3: 配置**

`core/config.py` 置信度段后追加：

```python
    # ---------- 局部重识别（S3-3）----------
    # 外扩 = max(PAD_MIN_PX, PAD_RATIO×行高)；放大 SCALE 倍，长边不超 1024
    reread_pad_min_px: int = 8
    reread_pad_ratio: float = 0.10
    reread_scale: float = 2.0
    # 单张票据重识别的总增量预算（API.md §2：≤1.5s，超出记 skipped）
    reread_budget_seconds: float = 1.5
```

```python
    @field_validator("reread_pad_min_px")
    @classmethod
    def _check_reread_pad(cls, value: int) -> int:
        if not 0 <= value <= 200:
            raise ValueError("REREAD_PAD_MIN_PX 必须在 0–200")
        return value

    @field_validator("reread_pad_ratio")
    @classmethod
    def _check_reread_ratio(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("REREAD_PAD_RATIO 必须在 [0,1]")
        return value

    @field_validator("reread_scale")
    @classmethod
    def _check_reread_scale(cls, value: float) -> float:
        if not 1.0 <= value <= 4.0:
            raise ValueError("REREAD_SCALE 必须在 [1,4]（再大就只是烧 CPU）")
        return value

    @field_validator("reread_budget_seconds")
    @classmethod
    def _check_reread_budget(cls, value: float) -> float:
        if not 0.1 <= value <= 10.0:
            raise ValueError("REREAD_BUDGET_SECONDS 必须在 [0.1,10]")
        return value
```

`.env.example` 追加：

```dotenv
# ---------- 局部重识别（S3-3）----------
# 外扩 = max(PAD_MIN_PX, PAD_RATIO×行高)；放大 SCALE 倍（长边上限 1024）
REREAD_PAD_MIN_PX=8
REREAD_PAD_RATIO=0.10
REREAD_SCALE=2.0
# 单张票据重识别总增量预算（秒），超出记 skipped（API.md §2：≤1.5s）
REREAD_BUDGET_SECONDS=1.5
```

- [ ] **Step 4: 测试与验证**

`tests/test_config.py` 追加：

```python
def test_reread_settings_defaults() -> None:
    settings = _settings()
    assert settings.reread_pad_min_px == 8
    assert settings.reread_pad_ratio == 0.10
    assert settings.reread_scale == 2.0
    assert settings.reread_budget_seconds == 1.5
```

`tests/test_models.py` 追加：

```python
def test_audit_action_check_includes_field_reread() -> None:
    from app.models.enums import AuditAction, enum_values

    assert "field_reread" in enum_values(AuditAction)
```

```bash
../.venv/Scripts/python.exe -m alembic upgrade head && ../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q tests/test_config.py tests/test_models.py tests/test_migration.py
```

- [ ] **Step 5: 提交**

```bash
git add docs/SCHEMA.md docs/API.md backend/app/models/enums.py \
  backend/alembic/versions/0005_audit_action_field_reread.py \
  backend/app/core/config.py backend/.env.example tests/test_config.py tests/test_models.py
git commit -m "feat(s3-3): field_reread 审计枚举（0005）与重识别配置"
```

---

### Task 2: `ocr/refine.py` 裁剪原语

**Files:**
- Create: `backend/app/ocr/refine.py`
- Test: `backend/tests/test_refine.py`（新）

**Interfaces:**
- Produces（Task 5/8 依赖）：
  ```python
  @dataclass(frozen=True)
  class RereadCandidate: text: str; score: float; bbox: tuple[int,int,int,int]; scale: float
  def points_to_bbox(points) -> tuple[int,int,int,int]
  def expand_bbox(bbox, *, width, height, pad_min_px=8, pad_ratio=0.10) -> tuple[int,int,int,int]
  def crop_region(image, bbox, *, pad_min_px=8, pad_ratio=0.10) -> tuple[np.ndarray, tuple[int,int,int,int]]
  def upscale(crop, *, factor=2.0, max_side=1024) -> np.ndarray
  def reread_box(image, box, *, engine=None, factor=2.0) -> RereadCandidate
  def bbox_to_original(bbox, to_original: np.ndarray | None) -> tuple[int,int,int,int]
  def bbox_from_original(bbox, to_original: np.ndarray | None) -> tuple[int,int,int,int]
  ```

- [ ] **Step 1: 写失败测试**

```python
"""S3-3 裁剪原语：外扩、clamp、缩放、坐标映射。

这些函数一旦错，表现是"重识别拿到的图不是那一行"——很难从结果反推，所以用纯 numpy 用例钉死。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.core.exceptions import ValidationFailedError
from app.ocr import refine


def _image(width: int = 200, height: int = 100) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_points_to_bbox_takes_outer_rectangle() -> None:
    points = [[10, 20], [90, 18], [92, 44], [8, 46]]
    assert refine.points_to_bbox(points) == (8, 18, 92, 46)


def test_expand_bbox_uses_max_of_pixels_and_ratio() -> None:
    # 行高 26 → 10% = 2.6 < 8 → 用 8px
    assert refine.expand_bbox((100, 100, 300, 126), width=800, height=600) == (92, 92, 308, 134)
    # 行高 100 → 10% = 10 > 8 → 用 10px
    assert refine.expand_bbox((100, 100, 300, 200), width=800, height=600) == (90, 90, 310, 210)


def test_expand_bbox_clamps_to_image_edges() -> None:
    assert refine.expand_bbox((2, 2, 30, 20), width=200, height=100) == (0, 0, 38, 28)


def test_expand_bbox_rejects_degenerate_bbox() -> None:
    with pytest.raises(ValidationFailedError):
        refine.expand_bbox((30, 10, 30, 40), width=200, height=100)


def test_crop_region_returns_crop_and_actual_bbox() -> None:
    image = _image()
    crop, bbox = refine.crop_region(image, (50, 40, 150, 70))

    assert bbox == (42, 32, 158, 78)
    assert crop.shape[:2] == (bbox[3] - bbox[1], bbox[2] - bbox[0])


def test_crop_region_outside_image_raises_422() -> None:
    with pytest.raises(ValidationFailedError):
        refine.crop_region(_image(), (500, 500, 600, 560))


def test_crop_region_negative_outside_raises() -> None:
    with pytest.raises(ValidationFailedError):
        refine.crop_region(_image(), (-50, -50, -20, -20))   # 与图无交集


def test_upscale_doubles_and_caps_long_side() -> None:
    crop = np.zeros((10, 20, 3), dtype=np.uint8)
    assert refine.upscale(crop, factor=2.0).shape[:2] == (20, 40)

    wide = np.zeros((10, 800, 3), dtype=np.uint8)
    enlarged = refine.upscale(wide, factor=2.0, max_side=1024)
    assert enlarged.shape[:2] == (13, 1024)      # 10×1.28=12.8 → 13


def test_reread_box_uses_recognizer(monkeypatch) -> None:
    calls: list[np.ndarray] = []

    def fake_recognize(crop, engine=None):
        calls.append(crop)
        return "¥1,234.56", 0.97

    monkeypatch.setattr(refine, "recognize_line", fake_recognize)
    candidate = refine.reread_box(_image(), [[50, 40], [150, 40], [150, 70], [50, 70]])

    assert candidate.text == "¥1,234.56"
    assert candidate.score == 0.97
    assert candidate.bbox == (42, 32, 158, 78)
    assert calls and calls[0].shape[0] == (78 - 32) * 2      # 已放大


def test_bbox_mapping_identity_when_no_matrix() -> None:
    assert refine.bbox_to_original((10, 20, 30, 40), None) == (10, 20, 30, 40)
    assert refine.bbox_from_original((10, 20, 30, 40), None) == (10, 20, 30, 40)


def test_bbox_mapping_scales_and_round_trips() -> None:
    matrix = np.array([[2.0, 0.0, 5.0], [0.0, 2.0, -3.0], [0.0, 0.0, 1.0]])

    assert refine.bbox_to_original((10, 20, 30, 40), matrix) == (25, 37, 65, 77)
    assert refine.bbox_from_original((25, 37, 65, 77), matrix) == (10, 20, 30, 40)
```

- [ ] **Step 2: 确认失败**：`../.venv/Scripts/python.exe -m pytest -q tests/test_refine.py`

预期 `ModuleNotFoundError: app.ocr.refine`。

- [ ] **Step 3: 实现**

```python
"""局部重识别原语（S3-3）：按 OCR 行框裁剪、放大、单行识别。

为什么单独一层：裁剪边界（越界/退化）是 500 与 422 的分水岭，必须可单测；
坐标映射（校正图 ↔ 原图）是 S3-5 画框与本卡共用的唯一实现。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from app.core.exceptions import ValidationFailedError
from app.ocr.recognizer import recognize_line

__all__ = [
    "RereadCandidate",
    "bbox_from_original",
    "bbox_to_original",
    "crop_region",
    "expand_bbox",
    "points_to_bbox",
    "reread_box",
    "upscale",
]

# 默认值；运行时的实际取值由 Settings（REREAD_*）传入
PAD_MIN_PX = 8
PAD_RATIO = 0.10
SCALE = 2.0
MAX_SIDE = 1024
MIN_CROP_PX = 8


@dataclass(frozen=True)
class RereadCandidate:
    text: str
    score: float
    bbox: tuple[int, int, int, int]
    scale: float


def points_to_bbox(points: Sequence[Sequence[float]]) -> tuple[int, int, int, int]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return int(round(min(xs))), int(round(min(ys))), int(round(max(xs))), int(round(max(ys)))


def expand_bbox(
    bbox: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
    pad_min_px: int = PAD_MIN_PX,
    pad_ratio: float = PAD_RATIO,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = (int(value) for value in bbox)
    if x2 <= x1 or y2 <= y1:
        raise ValidationFailedError("bbox 非法：宽高必须为正")
    # 以**行高**为基准外扩：宽票据按宽度 10% 扩会把相邻行也框进来
    pad = max(int(pad_min_px), int(round((y2 - y1) * pad_ratio)))
    return (
        max(0, x1 - pad),
        max(0, y1 - pad),
        min(int(width), x2 + pad),
        min(int(height), y2 + pad),
    )


def crop_region(
    image: np.ndarray,
    bbox: tuple[int, int, int, int],
    *,
    pad_min_px: int = PAD_MIN_PX,
    pad_ratio: float = PAD_RATIO,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    if image is None or image.ndim < 2:
        raise ValidationFailedError("影像不可用，无法裁剪")
    height, width = image.shape[:2]
    actual = expand_bbox(bbox, width=width, height=height,
                         pad_min_px=pad_min_px, pad_ratio=pad_ratio)
    x1, y1, x2, y2 = actual
    if x2 - x1 < MIN_CROP_PX or y2 - y1 < MIN_CROP_PX:
        raise ValidationFailedError("裁剪区域为空或过小（bbox 越界？）")
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValidationFailedError("裁剪区域为空（bbox 越界？）")
    return crop, actual


def upscale(crop: np.ndarray, *, factor: float = SCALE, max_side: int = MAX_SIDE) -> np.ndarray:
    height, width = crop.shape[:2]
    longest = max(height, width)
    if longest == 0:
        raise ValidationFailedError("裁剪图为空")
    scale = min(float(factor), max_side / longest)
    if scale <= 1.0:
        return crop
    return cv2.resize(
        crop,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_CUBIC,
    )


def reread_box(
    image: np.ndarray,
    box: Sequence[Sequence[float]] | tuple[int, int, int, int],
    *,
    engine=None,
    factor: float = SCALE,
    pad_min_px: int = PAD_MIN_PX,
    pad_ratio: float = PAD_RATIO,
) -> RereadCandidate:
    bbox = points_to_bbox(box) if len(box) == 4 and isinstance(box[0], (list, tuple)) else tuple(box)  # type: ignore[arg-type]
    crop, actual = crop_region(image, bbox, pad_min_px=pad_min_px, pad_ratio=pad_ratio)
    enlarged = upscale(crop, factor=factor)
    text, score = recognize_line(enlarged, engine)
    return RereadCandidate(text=text, score=float(score), bbox=actual, scale=factor)


def _apply(bbox: tuple[int, int, int, int], matrix: np.ndarray) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    corners = np.array([[x1, y1, 1.0], [x2, y1, 1.0], [x2, y2, 1.0], [x1, y2, 1.0]])
    mapped = corners @ matrix.T
    xs = mapped[:, 0] / mapped[:, 2]
    ys = mapped[:, 1] / mapped[:, 2]
    return int(round(xs.min())), int(round(ys.min())), int(round(xs.max())), int(round(ys.max()))


def bbox_to_original(
    bbox: tuple[int, int, int, int], to_original: np.ndarray | None
) -> tuple[int, int, int, int]:
    """校正图 → 原图。PDF 没有预处理，矩阵为 None，恒等返回。"""
    return bbox if to_original is None else _apply(bbox, np.asarray(to_original, dtype=float))


def bbox_from_original(
    bbox: tuple[int, int, int, int], to_original: np.ndarray | None
) -> tuple[int, int, int, int]:
    """原图 → 校正图（区域裁剪要用）。"""
    if to_original is None:
        return bbox
    return _apply(bbox, np.linalg.inv(np.asarray(to_original, dtype=float)))
```

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_refine.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/ocr/refine.py backend/tests/test_refine.py
git commit -m "feat(s3-3): 裁剪原语 refine.py（外扩/clamp/放大/坐标映射）"
```

---

### Task 3: 修 `invoice_fields.bbox_json` 坐标（原图）

**Files:**
- Modify: `backend/app/services/ocr_pipeline.py`
- Test: `backend/tests/test_ocr_pipeline.py`

**Interfaces:**
- `field_rows(..., original_bboxes: dict[str, list[int]] | None = None)`

- [ ] **Step 1: 写失败测试**（`tests/test_ocr_pipeline.py` 集成区）

```python
class _FakePreprocess:
    """只带 to_original 的假预处理结果：2× 放大 + 平移 (10, -5)。"""

    to_original = np.array([[0.5, 0.0, 10.0], [0.0, 0.5, -5.0], [0.0, 0.0, 1.0]])


def _page_with_preprocess(page_no: int = 1):
    from app.ocr.intake import PreparedPage

    import numpy as np

    return PreparedPage(
        page_no=page_no,
        image=np.zeros((600, 1000, 3), dtype=np.uint8),
        kind="photo",
        quality_flag="ok",
        preprocess=_FakePreprocess(),
    )


@pytest.mark.integration
def test_run_pipeline_maps_field_bbox_to_original(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    deps = PipelineDeps(
        engine=FakeEngine({0: _vat_digital_lines()}),
        storage=FakeStorage(),
        prepare_pages=lambda *args, **kwargs: [_page_with_preprocess()],
        templates=None,
    )
    outcome, _ = _execute(session_factory, invoice_id, deps)

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        row = db.scalar(select(InvoiceField).where(
            InvoiceField.invoice_id == invoice_id,
            InvoiceField.field_name == "invoice_no",
        ))
        # ocr_lines 行框 = [10, 120, 600, 150]（校正图，invoice_no 在第 2 行）→ 0.5× + 平移(10,-5)
        assert row.bbox_json == [15, 55, 310, 70]
```

（`_line` 默认 box 为 `[[10,y],[600,y],[600,y+30],[10,y+30]]`；线性映射后取整即为上面的值；
若实现改四角取整顺序，按实际值调整断言并在提交信息里说明。）

- [ ] **Step 2: 确认失败**：`..\..\.venv\Scripts\python.exe -m pytest -m integration -q tests/test_ocr_pipeline.py -k original`

- [ ] **Step 3: 实现**

1. `ocr_pipeline.py` 顶部：
   ```python
   from app.ocr.refine import bbox_to_original
   ```
2. `run_pipeline` 在调用 `_persist` 前构造映射：
   ```python
        page_by_no = {page.page_no: page for page in pages}

        def _original_bbox(name: str) -> list[int] | None:
            item = merged.get(name)
            if item is None or item.field.bbox is None:
                return None
            page = page_by_no.get(item.page_no)
            matrix = (
                page.prepared.preprocess.to_original
                if page is not None and page.prepared.preprocess is not None
                else None
            )
            return list(bbox_to_original(tuple(item.field.bbox), matrix))

        original_bboxes = {name: bbox for name in merged if (bbox := _original_bbox(name))}
   ```
3. `_persist` 增参 `original_bboxes: dict[str, list[int]]` 并透传给 `field_rows`；
   `field_rows` 增可选参数（默认 `None`）：
   ```python
                "bbox_json": (
                    original_bboxes.get(name)
                    if original_bboxes is not None and name in original_bboxes
                    else (list(extracted.bbox) if extracted.bbox is not None else None)
                ),
   ```

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_pipeline.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_ocr_pipeline.py
../.venv/Scripts/python.exe -m pytest -q
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/ocr_pipeline.py backend/tests/test_ocr_pipeline.py
git commit -m "fix(s3-3): invoice_fields.bbox 写原图坐标（S3-1 偏差）"
```

---

### Task 4: 提交接口（schema + service.submit + 路由）

**Files:**
- Modify: `backend/app/schemas/invoice.py`
- Create: `backend/app/services/reocr_service.py`（先实现 `submit_reocr` 与 `ReocrDeps`）
- Modify: `backend/app/api/v1/invoices.py`
- Test: `backend/tests/test_reocr.py`（新）

**Interfaces:**
- Produces:
  ```python
  class ReocrRequest(BaseModel): fields: list[str]; version: int | None
  class ReocrData(BaseModel): invoice_id: int; job_id: int; fields: list[str]
  def submit_reocr(db, user, invoice_id, payload) -> ReocrData
  ```

- [ ] **Step 1: 写失败测试**（`tests/test_reocr.py`，临时库 + 假队列，夹具照 `test_invoices.py`/`test_jobs.py`）

```python
"""S3-3 reocr 提交侧：权限、状态、字段校验、入队失败补偿。"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from decimal import Decimal

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.enums import FieldSource
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.services import invoice_service
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/invoices"


class FakeQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.queued: list[int] = []
        self.fail = fail

    async def enqueue_ocr(self, job_id: int) -> None:
        await self.enqueue_reocr(job_id)

    async def enqueue_reocr(self, job_id: int) -> None:
        if self.fail:
            raise RuntimeError("queue down")
        self.queued.append(job_id)


@pytest.fixture(scope="module")
def reocr_database() -> Iterator[URL]:
    with temporary_database("study_ocr_reocr_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(reocr_database: URL):
    eng = create_engine(reocr_database.render_as_string(hide_password=False), poolclass=NullPool)
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


@pytest.fixture()
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture()
def client(session_factory, clean_db, fake_queue) -> Iterator[TestClient]:
    from app.api.deps import get_task_queue

    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_task_queue] = lambda: fake_queue
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(user_id=user.id, role=user.role,
                            token_type=TokenType.ACCESS, ttl_seconds=60)
    return {"Authorization": f"Bearer {token}"}


def _make_invoice_with_fields(session_factory, uploader_id: int, **fields) -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin")
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=uploader_id, file_id=file.id)
        db.add(InvoiceField(invoice_id=invoice.id, field_name="total_amount",
                            field_value="100.00", normalized_value="100.00",
                            raw_text="¥100.00", source="ocr", confidence=Decimal("0.700"),
                            bbox_json=[10, 20, 300, 60], source_page_no=1, source_line_no=3))
        db.commit()
        return invoice.id


def test_reocr_submits_job_and_enqueues(client, session_factory, actors, fake_queue) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)

    response = client.post(f"{BASE}/{invoice_id}/reocr",
                           json={"fields": ["total_amount"]}, headers=_auth(actors["alice"]))

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["invoice_id"] == invoice_id and data["fields"] == ["total_amount"]
    assert fake_queue.queued == [data["job_id"]]
    with session_factory() as db:
        job = db.get(OcrJob, data["job_id"])
        assert job.stage == "recognize" and job.params_json["kind"] == "reocr"


def test_reocr_other_submitter_404(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    assert client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                       headers=_auth(actors["bob"])).status_code == 404


def test_reocr_reviewer_forbidden_per_contract(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    assert client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                       headers=_auth(actors["rita"])).status_code == 403


def test_reocr_rejects_non_draft(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    with session_factory() as db:
        db.execute(sa_text("update invoices set status='confirmed' where id=:id"), {"id": invoice_id})
        db.commit()
    assert client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                       headers=_auth(actors["alice"])).status_code == 409


@pytest.mark.parametrize(
    "setup",
    ["unknown_field", "manual_field", "already_retried", "no_bbox"],
)
def test_reocr_rejects_invalid_fields(client, session_factory, actors, setup) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    with session_factory() as db:
        row = db.scalar(select(InvoiceField).where(InvoiceField.invoice_id == invoice_id))
        if setup == "manual_field":
            row.source = FieldSource.MANUAL.value
        elif setup == "already_retried":
            row.source = FieldSource.OCR_RETRY.value
        elif setup == "no_bbox":
            row.bbox_json = None
        db.commit()
    field = "total_amount" if setup != "unknown_field" else "not_a_field"
    assert client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": [field]},
                       headers=_auth(actors["alice"])).status_code == 422


def test_reocr_rejects_duplicate_running_job(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    first = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                        headers=_auth(actors["alice"]))
    assert first.status_code == 202
    second = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                         headers=_auth(actors["alice"]))
    assert second.status_code == 409


def test_reocr_queue_failure_marks_job_failed(client, session_factory, actors) -> None:
    fake_queue.fail = True
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    response = client.post(f"{BASE}/{invoice_id}/reocr", json={"fields": ["total_amount"]},
                           headers=_auth(actors["alice"]))
    assert response.status_code == 503


def test_reocr_request_validation(client, session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    for body in ({"fields": []}, {"fields": ["a"] * 7}, {"fields": ["total_amount", "total_amount"]}):
        assert client.post(f"{BASE}/{invoice_id}/reocr", json=body,
                           headers=_auth(actors["alice"])).status_code == 422
```

- [ ] **Step 2: 确认失败**：`../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_reocr.py`

- [ ] **Step 3: 实现 schema**

`schemas/invoice.py` 末尾：

```python
class ReocrRequest(BaseModel):
    """局部二次识别入参（API.md §2）：1–6 个字段。"""

    fields: list[str] = Field(min_length=1, max_length=6)
    version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _reject_duplicate_fields(self) -> "ReocrRequest":
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("fields 里不能有重复的 field_name")
        return self


class ReocrData(BaseModel):
    invoice_id: int
    job_id: int
    fields: list[str]
```

- [ ] **Step 4: 实现 `submit_reocr`**

`services/reocr_service.py`：

```python
"""局部二次识别（S3-3）：提交侧校验 + worker 侧执行。

契约是**异步** `POST /invoices/{id}/reocr`（API.md §2）：提交只建作业并入队，
真正的裁剪识别在 worker 里做。写回原则：**只更新 raw_text/confidence/source=ocr_retry，
绝不改 field_value**（人工选择值归 S3-6，D-060）。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, RoleForbiddenError, ValidationFailedError
from app.models.enums import ExtractStatus, FieldSource, InvoiceStatus, OcrStage, OcrStatus
from app.models.invoice_fields import InvoiceField
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.schemas.invoice import ReocrData
from app.services import job_service
from app.services.repository import get_visible_or_404

logger = logging.getLogger("app.reocr")

_REOCR_STAGE = OcrStage.RECOGNIZE.value
_ACTIVE = (OcrStatus.PENDING.value, OcrStatus.RUNNING.value)


def _in_progress_job(db: Session, invoice_id: int) -> int | None:
    return db.scalar(
        select(OcrJob.id).where(
            OcrJob.invoice_id == invoice_id,
            OcrJob.stage == _REOCR_STAGE,
            OcrJob.status.in_(_ACTIVE),
            OcrJob.params_json["kind"].astext == "reocr",
        ).limit(1)
    )


def submit_reocr(db: Session, user: User, invoice_id: int, payload) -> ReocrData:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    if invoice.uploader_id != user.id:
        # 契约 §2 只写"本人"；reviewer 触发权限与 S3-6 一起定（遗留）
        raise RoleForbiddenError("只有票据本人可以触发重识别")

    if invoice.status != InvoiceStatus.DRAFT.value or invoice.extract_status not in (
        ExtractStatus.RAW.value, ExtractStatus.NEED_CONFIRM.value,
    ):
        raise ConflictError(f"当前状态（{invoice.status}/{invoice.extract_status}）不允许重识别")
    if payload.version is not None and payload.version != invoice.version:
        raise ConflictError(f"版本冲突：当前 version={invoice.version}")

    rows = {row.field_name: row for row in db.scalars(
        select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)).all()}
    problems: list[str] = []
    for name in payload.fields:
        row = rows.get(name)
        if row is None:
            problems.append(f"{name}：字段不存在")
        elif row.source == FieldSource.MANUAL.value or row.confirmed:
            problems.append(f"{name}：人工值不可重识别")
        elif row.source == FieldSource.OCR_RETRY.value:
            problems.append(f"{name}：已重识别过")
        elif row.bbox_json is None or row.source_line_no is None:
            problems.append(f"{name}：缺少定位信息")
    if problems:
        raise ValidationFailedError("；".join(problems))

    if _in_progress_job(db, invoice.id) is not None:
        raise ConflictError("该票据已有进行中的重识别")

    job = job_service.create_job(db, invoice_id=invoice.id, stage=_REOCR_STAGE)
    job.params_json = {"kind": "reocr", "fields": list(payload.fields)}
    db.commit()
    db.refresh(job)
    logger.info("重识别已受理", extra={"event": "reocr_submitted",
                                        "invoice_id": invoice.id, "task_id": job.id})
    return ReocrData(invoice_id=invoice.id, job_id=job.id, fields=list(payload.fields))
```

- [ ] **Step 5: 实现路由**

`api/v1/invoices.py`：

```python
from fastapi import APIRouter, Depends, Query, Response, status

@router.post(
    "/{invoice_id}/reocr",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=Envelope[ReocrData],
    summary="局部二次识别（按字段裁剪放大重识别，异步）",
)
async def reocr_invoice(
    invoice_id: int,
    payload: ReocrRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    queue: OcrTaskQueue = Depends(get_task_queue),
) -> dict:
    data = await asyncio.to_thread(reocr_service.submit_reocr, db, user, invoice_id, payload)
    try:
        await queue.enqueue_reocr(data.job_id)
    except Exception:
        await asyncio.to_thread(job_service.mark_enqueue_failed, data.job_id, db=db)
        raise ServiceUnavailableError("任务队列不可用") from None
    return ok(data, "accepted")
```

（顶部补 `import asyncio`、`get_task_queue`/`OcrTaskQueue`、`ServiceUnavailableError`、`job_service`、
`reocr_service`、`ReocrRequest/ReocrData` 导入。）

- [ ] **Step 6: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_reocr.py
../.venv/Scripts/python.exe -m pytest -q tests/test_api_docs.py
```

- [ ] **Step 7: 提交**

```bash
git add backend/app/schemas/invoice.py backend/app/services/reocr_service.py \
  backend/app/api/v1/invoices.py backend/tests/test_reocr.py
git commit -m "feat(s3-3): POST /invoices/{id}/reocr 提交接口（校验+建作业+入队）"
```

---

### Task 5: worker 执行（`run_reocr` + 任务 + 队列）

**Files:**
- Modify: `backend/app/services/reocr_service.py`（追加 `ReocrDeps` / `run_reocr`）
- Modify: `backend/app/tasks/{ocr_task,queue,worker}.py`
- Test: `backend/tests/test_reocr.py`（追加集成用例）

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class ReocrDeps: engine=None; storage=None; prepare_pages=None; clock=time.monotonic
  def run_reocr(db, job, *, deps: ReocrDeps | None = None) -> None
  ```

- [ ] **Step 1: 写失败测试**（`tests/test_reocr.py` 追加；用假 engine/storage/prepare，直接调 `run_reocr`；
  顶部补 `from app.models.enums import OcrStatus`）

```python
class _FakeEngine:
    def __init__(self, text: str, score: float) -> None:
        self.text, self.score = text, score

    def recognize_line(self, crop):
        return self.text, self.score


class _FakeStorage:
    def get_bytes(self, key, *, max_bytes):
        return b"fake-image"


def _deps(text: str, score: float, *, clock=None):
    from app.services.reocr_service import ReocrDeps
    from app.ocr.intake import PreparedPage
    import numpy as np

    page = PreparedPage(page_no=1, image=np.zeros((600, 1000, 3), dtype=np.uint8),
                        kind="pdf", quality_flag=None)
    return ReocrDeps(
        engine=_FakeEngine(text, score),
        storage=_FakeStorage(),
        prepare_pages=lambda *args, **kwargs: [page],
        clock=clock,
    )


def _run(session_factory, job_id, deps):
    from app.services.reocr_service import run_reocr

    with session_factory() as db:
        job = db.get(OcrJob, job_id)
        job.status = OcrStatus.RUNNING.value
        job.retry_count = 1
        db.commit()
        db.refresh(job)
        run_reocr(db, job, deps=deps)
        job.status = OcrStatus.SUCCEEDED.value     # 模拟 job_service 写终态
        db.commit()


def _submit(session_factory, invoice_id: int, user_id: int) -> int:
    from app.schemas.invoice import ReocrRequest
    from app.services.reocr_service import submit_reocr

    with session_factory() as db:
        user = db.get(User, user_id)
        return submit_reocr(db, user, invoice_id, ReocrRequest(fields=["total_amount"])).job_id


@pytest.mark.integration
def test_run_reocr_updates_raw_text_and_confidence_only(session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    job_id = _submit(session_factory, invoice_id, actors["alice"].id)

    _run(session_factory, job_id, _deps("¥100.00", 0.96))

    with session_factory() as db:
        row = db.scalar(select(InvoiceField).where(InvoiceField.invoice_id == invoice_id))
        job = db.get(OcrJob, job_id)
        assert str(row.confidence) == "0.960"
        assert row.raw_text == "¥100.00"
        assert row.field_value == "100.00"                 # 值不变
        assert row.normalized_value == "100.00"
        assert row.source == "ocr_retry"
        assert row.need_review is False                     # 0.96 ≥ 0.90
        assert job.metrics["applied"] == 1
        assert job.metrics["fields"][0]["new_score"] == 0.96


@pytest.mark.integration
def test_run_reocr_skips_when_not_improved(session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    job_id = _submit(session_factory, invoice_id, actors["alice"].id)

    _run(session_factory, job_id, _deps("¥100.00", 0.50))

    with session_factory() as db:
        row = db.scalar(select(InvoiceField).where(InvoiceField.invoice_id == invoice_id))
        assert str(row.confidence) == "0.700" and row.source == "ocr"
        assert db.get(OcrJob, job_id).metrics["skipped"] == 1


@pytest.mark.integration
def test_run_reocr_skips_empty_text_and_budget(session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    job_id = _submit(session_factory, invoice_id, actors["alice"].id)

    _run(session_factory, job_id, _deps("", 0.0))

    with session_factory() as db:
        metrics = db.get(OcrJob, job_id).metrics
        assert metrics["fields"][0]["reason"] == "no_text"

    # 预算：clock 第一次返回 0、之后立刻超时
    job_id = _submit(session_factory, invoice_id, actors["alice"].id)
    job_id2 = _submit(session_factory, invoice_id, actors["alice"].id)
    ticks = iter([0.0, 10.0, 10.0])   # started=0，第一次字段检查已超 1.5s 预算
    _run(session_factory, job_id2, _deps("¥100.00", 0.99, clock=lambda: next(ticks, 100.0)))
    with session_factory() as db:
        assert db.get(OcrJob, job_id2).metrics["fields"][0]["reason"] == "budget"


@pytest.mark.integration
def test_run_reocr_recomputes_invoice_overall(session_factory, actors) -> None:
    invoice_id = _make_invoice_with_fields(session_factory, actors["alice"].id)
    job_id = _submit(session_factory, invoice_id, actors["alice"].id)
    _run(session_factory, job_id, _deps("¥100.00", 0.96))
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        assert str(invoice.overall_confidence) == "0.960"
        assert invoice.extract_status == "raw"          # 全部 auto


def test_reocr_task_registered_and_queue_uses_reocr_task() -> None:
    from app.tasks.worker import WorkerSettings

    names = [getattr(f, "name", None) or f.__name__ for f in WorkerSettings.functions]
    assert "reocr_task" in names


async def test_queue_enqueues_reocr_task(monkeypatch) -> None:
    from app.tasks import queue as queue_module

    calls: list[tuple[str, int]] = []

    class _Pool:
        async def enqueue_job(self, name, job_id):
            calls.append((name, job_id))

        async def close(self) -> None:
            return None

    async def _fake_pool(redis_settings):
        return _Pool()

    monkeypatch.setattr(queue_module, "create_pool", _fake_pool)
    from arq.connections import RedisSettings

    await queue_module.ArqOcrTaskQueue(RedisSettings()).enqueue_reocr(42)
    assert calls == [("reocr_task", 42)]
```

- [ ] **Step 2: 确认失败**：`../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_reocr.py -k run_reocr`

- [ ] **Step 3: 实现 `run_reocr`**

追加到 `reocr_service.py`：

```python
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np

from app.models.invoice_files import InvoiceFile
from app.models.ocr_lines import OcrLine as OcrLineRow
from app.ocr import refine
from app.ocr.intake import prepare_pages
from app.ocr.detector import OcrOptions, get_engine
from app.services.confidence import FieldConfidence, aggregate_invoice, level_for
from app.services.storage import get_storage

TEMP_PREFIX = "reocr-"
MIN_TEXT_SCORE = 0.3


@dataclass
class ReocrDeps:
    engine: object | None = None
    storage: object | None = None
    prepare_pages: object | None = None
    clock: object = time.monotonic


def run_reocr(db: Session, job: OcrJob, *, deps: ReocrDeps | None = None) -> None:
    deps = deps or ReocrDeps()
    settings = get_settings()
    fields = list((job.params_json or {}).get("fields") or [])
    started = deps.clock()
    deadline = started + settings.reread_budget_seconds

    invoice = db.get(Invoice, job.invoice_id)
    if invoice is None or invoice.status != InvoiceStatus.DRAFT.value:
        raise ValidationFailedError("票据不存在或已不在草稿态，重识别取消")
    file = db.get(InvoiceFile, invoice.file_id)
    if file is None:
        raise ValidationFailedError("票据文件记录不存在")

    rows = {row.field_name: row for row in db.scalars(
        select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)).all()}
    pages_needed = max((rows[name].source_page_no or 1) for name in fields if name in rows) if fields else 1

    storage = deps.storage or get_storage()
    engine = deps.engine or get_engine(OcrOptions.from_settings(settings))
    prepare = deps.prepare_pages or prepare_pages
    source = storage.get_bytes(file.storage_key, max_bytes=settings.max_upload_mb * 1024 * 1024)
    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
        path = Path(tmp) / "source.bin"
        path.write_bytes(source)
        prepared = prepare(path, max_pages=pages_needed, pdf_dpi=250)
    page_by_no = {page.page_no: page for page in prepared}

    results: list[dict] = []
    applied = skipped = 0
    for name in fields:
        row = rows.get(name)
        if row is None:
            results.append({"field_name": name, "reason": "missing_field"})
            skipped += 1
            continue
        if deps.clock() > deadline:
            results.append({"field_name": name, "reason": "budget"})
            skipped += 1
            continue
        line = db.scalar(select(OcrLineRow).where(
            OcrLineRow.invoice_id == invoice.id,
            OcrLineRow.page_no == (row.source_page_no or 1),
            OcrLineRow.line_no == row.source_line_no,
        ))
        page = page_by_no.get(row.source_page_no or 1)
        if line is None or not line.box_json or page is None:
            results.append({"field_name": name, "reason": "missing_line"})
            skipped += 1
            continue

        old_confidence = float(row.confidence or 0)
        candidate = refine.reread_box(
            page.image, line.box_json, engine=engine,
            factor=settings.reread_scale,
            pad_min_px=settings.reread_pad_min_px,
            pad_ratio=settings.reread_pad_ratio,
        )
        if not candidate.text.strip() or candidate.score < MIN_TEXT_SCORE:
            results.append({"field_name": name, "reason": "no_text", "new_text": candidate.text,
                            "new_score": candidate.score, "bbox": list(candidate.bbox)})
            skipped += 1
            continue

        did_apply = candidate.score > old_confidence
        if did_apply:
            row.raw_text = candidate.text
            row.confidence = Decimal(str(round(candidate.score, 3)))
            row.source = FieldSource.OCR_RETRY.value
            row.need_review = level_for(candidate.score) != "auto"
            row.updated_at = datetime.now(timezone.utc)
            applied += 1
        else:
            skipped += 1
        results.append({
            "field_name": name,
            "old_value": row.field_value,
            "old_confidence": old_confidence,
            "new_text": candidate.text,
            "new_score": candidate.score,
            "applied": did_apply,
            "reason": None if did_apply else "not_improved",
            "bbox": list(candidate.bbox),
        })

    # 整单分与抽取状态重算（need_review 以库内 confidence 为准）
    all_rows = db.scalars(select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)).all()
    levels = [
        FieldConfidence(row.field_name, float(row.confidence or 0),
                        level_for(float(row.confidence or 0)))
        for row in all_rows if row.confidence is not None
    ]
    aggregate = aggregate_invoice(levels)
    invoice.overall_confidence = Decimal(str(aggregate.overall))
    invoice.extract_status = (
        ExtractStatus.NEED_CONFIRM.value if aggregate.needs_review
        else ExtractStatus.RAW.value
    )
    db.add(invoice)

    job.metrics = {
        "kind": "reocr",
        "budget_ms": int(settings.reread_budget_seconds * 1000),
        "elapsed_ms": int((deps.clock() - started) * 1000),
        "applied": applied,
        "skipped": skipped,
        "fields": results,
        "overall_confidence": aggregate.overall,
    }
```

- [ ] **Step 4: 任务与队列**

`tasks/ocr_task.py` 增：

```python
async def reocr_task(ctx: dict, job_id: int) -> dict:
    attempt = int(ctx.get("job_try", 1) or 1)
    try:
        outcome = await asyncio.to_thread(
            job_service.execute_job_attempt,
            job_id, attempt=attempt, handler=_run_reocr,
        )
    except job_service.RetryLater as exc:
        raise Retry(defer=exc.delay) from None
    return {"job_id": job_id, "attempt": attempt, "outcome": outcome}


def _run_reocr(db, job) -> None:
    from app.services.reocr_service import run_reocr

    run_reocr(db, job)
```

`tasks/queue.py`：协议加 `enqueue_reocr`；把入队逻辑抽成共用私有方法：

```python
class OcrTaskQueue(Protocol):
    async def enqueue_ocr(self, job_id: int) -> None: ...
    async def enqueue_reocr(self, job_id: int) -> None: ...


class ArqOcrTaskQueue:
    async def enqueue_ocr(self, job_id: int) -> None:
        await self._enqueue("ocr_task", job_id, event="job_enqueued")

    async def enqueue_reocr(self, job_id: int) -> None:
        await self._enqueue("reocr_task", job_id, event="reocr_enqueued")

    async def _enqueue(self, function: str, job_id: int, *, event: str) -> None:
        ...  # 原 enqueue_ocr 的连接/异常/close 逻辑，enqueue_job(function, job_id)
```

`tasks/worker.py`：`functions = [ocr_task, reocr_task, anchor_audit_task]`。

- [ ] **Step 5: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_reocr.py
../.venv/Scripts/python.exe -m pytest -q tests/test_jobs.py tests/test_ocr_pipeline.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/reocr_service.py backend/app/tasks backend/tests/test_reocr.py
git commit -m "feat(s3-3): reocr worker 执行（裁剪重识别/写回/重算整单）"
```

---

### Task 6: 前端"重识别"按钮

**Files:**
- Modify: `web/src/types/invoice.ts`、`web/src/api/invoices.ts`、`web/src/views/InvoiceDetail.vue`

- [ ] **Step 1: 类型与 API**

```ts
export interface ReocrData {
  invoice_id: number
  job_id: number
  fields: string[]
}
```

`api/invoices.ts`：

```ts
export async function reocrFields(
  invoiceId: number,
  fields: string[],
  version?: number,
): Promise<ReocrData> {
  const envelope = await post<ReocrData>(`/invoices/${invoiceId}/reocr`, { fields, version }, { silent: true })
  return unwrap(envelope)
}
```

- [ ] **Step 2: 详情页按钮**

- 字段表"操作"列加按钮（已有编辑/确认按钮则并排）：
  ```vue
  <el-button
    v-if="canReread(row)"
    size="small"
    text
    :loading="reocring.has(row.field_name)"
    @click="rereadField(row)"
  >重识别</el-button>
  ```
  并给 `source==='ocr_retry'` 的行加"已重识别"标签。
- 逻辑：
  ```ts
  const reocring = ref(new Set<string>())

  function canReread(row: InvoiceField): boolean {
    const invoice = detail.value?.invoice
    if (!invoice || invoice.status !== 'draft') return false
    if (row.source === 'manual' || row.confirmed || row.source === 'ocr_retry') return false
    return row.source_line_no !== null
  }

  async function rereadField(row: InvoiceField): Promise<void> {
    const invoice = detail.value?.invoice
    if (!invoice) return
    reocring.value.add(row.field_name)
    try {
      await reocrFields(invoice.id, [row.field_name], invoice.version)
      ElMessage.success('已提交重识别，稍后自动刷新')
      for (let attempt = 0; attempt < 5; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000))
        await load()
        if (detail.value?.invoice.status === 'draft') break
      }
    } catch (error) {
      ElMessage.error(error instanceof ApiError ? error.message : '重识别失败')
    } finally {
      reocring.value.delete(row.field_name)
    }
  }
  ```
  （轮询次数/间隔写死可接受：`reocr` 单次 ≤1.5s，2s×5 足够；失败只提示不重试。）

- [ ] **Step 3: 验证**

```bash
cd /e/BiSheXZY/study-OCR/web && npm run typecheck && npm run build
```

- [ ] **Step 4: 提交**

```bash
git add web/src/types/invoice.ts web/src/api/invoices.ts web/src/views/InvoiceDetail.vue
git commit -m "feat(s3-3): 详情页字段重识别按钮"
```

---

### Task 7: 验收脚本 + 40 张实测 + HTTP 冒烟

**Files:**
- Create: `scripts/reread_eval.py`

- [ ] **Step 1: 写脚本**（离线；不写库；与 `confidence_eval.py` 同样的 `sys.path` 处理）

```python
"""S3-3 局部重识别验收：N 张合成票据离线跑，挑低置信字段裁剪重识别。

用法：
    .venv/Scripts/python.exe scripts/reread_eval.py --samples 40
口径：
- 低置信字段 = 全链路抽取后 confidence < 0.90 且有 source_line_no 的字段；
- 对每个字段按 refine.reread_box 做一次重识别，记录 old/new 文本与分数；
- 提升 = new_score > old_confidence + 0.05；
输出：可重识别字段数、提升数/率、平均提升、top 5 样例、错误列表；
JSON 写 backend/eval/results/reread_eval_<ts>.json。
"""
```

实现要点：

```python
records = []
for path in samples:
    page = prepare_pages(path, max_pages=1)[0]
    ocr = engine.recognize(page.image)
    layout = analyze_layout(ocr.lines, ocr.image_size, templates)
    extracted = extract_fields(ocr.lines, ocr.image_size, layout.ticket_type, layout, rules)
    for name, field in extracted.fields.items():
        if field.confidence >= 0.90 or field.source_line_no is None:
            continue
        line = next((item for item in ocr.lines if item.line_no == field.source_line_no), None)
        if line is None:
            continue
        try:
            candidate = refine.reread_box(page.image, line.box, engine=engine)
        except ValidationFailedError as exc:
            records.append({"file": path.name, "field": name, "error": str(exc)})
            continue
        records.append({
            "file": path.name, "field": name,
            "old_value": field.value, "old_confidence": field.confidence,
            "new_text": candidate.text, "new_score": candidate.score,
            "improved": candidate.score > float(field.confidence) + 0.05,
            "delta": round(candidate.score - float(field.confidence), 3),
            "bbox": list(candidate.bbox),
        })
```

统计与打印：`candidates`、`improved`、`improve_rate`、`avg_delta`、`errors`、按字段名的分组计数；
`--out` 写 JSON；退出码 0（只要没有崩溃；提升率不达标如实打印，不用退出码冒充验收）。

- [ ] **Step 2: 跑 40 张**

```bash
cd /e/BiSheXZY/study-OCR && .venv/Scripts/python.exe scripts/reread_eval.py --samples 40
```

把实测提升率写进 PROGRESS（Task 8），**不美化**：如果没有提升，如实写"合成集上裁剪重识别无显著提升"。

- [ ] **Step 3: HTTP 冒烟（起 uvicorn + worker + 临时账号）**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe ../scripts/create_user.py --username s33sub --role submitter \
  --real-name S33验收 --password 'S33sub!2026'
# 另开两个终端：uvicorn :8000 + arq worker
cd /e/BiSheXZY/study-OCR && .venv/Scripts/python.exe - <<'PYEOF'
import time, httpx
from pathlib import Path
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=120)
tok = c.post("/api/v1/auth/login",
             json={"username": "s33sub", "password": "S33sub!2026"}).json()["data"]["access_token"]
H = {"Authorization": f"Bearer {tok}"}
with Path("samples/synth_v1/train_ticket_portrait_strip_001.jpg").open("rb") as handle:
    up = c.post("/api/v1/invoices/upload", headers=H,
                files={"file": ("a.jpg", handle, "image/jpeg")})
iid = up.json()["data"]["invoice_id"]
for _ in range(60):
    status = c.get(f"/api/v1/invoices/{iid}/ocr-status", headers=H).json()["data"]
    if status["ocr_status"] in ("succeeded", "failed"):
        break
    time.sleep(2)
detail = c.get(f"/api/v1/invoices/{iid}", headers=H).json()["data"]
low = [f for f in detail["fields"]
       if f["source_line_no"] is not None and float(f["confidence"] or 0) < 0.9]
print("low:", [(f["field_name"], f["confidence"]) for f in low])
resp = c.post(f"/api/v1/invoices/{iid}/reocr", headers=H,
              json={"fields": [f["field_name"] for f in low[:2]]})
print("reocr:", resp.status_code, resp.json().get("data"))
time.sleep(5)
after = c.get(f"/api/v1/invoices/{iid}", headers=H).json()["data"]
print("after:", [(f["field_name"], f["confidence"], f["source"], f["raw_text"])
                 for f in after["fields"]])
print("invoice_id", iid)
PYEOF
```

冒烟后清理（`cleanup([iid])` + 删临时账号，同 S3-1/S3-2 做法）。

- [ ] **Step 4: 提交**

```bash
git add scripts/reread_eval.py
git commit -m "feat(s3-3): 局部重识别验收脚本（提升率与样例）"
```

---

### Task 8: 全量回归 + 文档收口

- [ ] **Step 1: 全量验证**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
```

- [ ] **Step 2: 文档**

- `PROGRESS`：勾 S3-3 + 表格行（产出、reread_eval 提升率、HTTP 冒烟、测试计数）；
  新增遗留：`field_reread` 审计行待 S4-7、reviewer 触发权限待 S3-6、region 重识别未做（契约无入口）；
- `DECISIONS`：
  - **D-060** 重识别接口按契约异步 `reocr`；写回只改 `raw_text/confidence/source=ocr_retry` 不改值；
    字段 bbox 修正为原图坐标（`ocr_lines` 保持校正图）；
  - **D-061** 外扩 `max(8px, 10%×行高)`、放大 2×（长边 1024）、1.5s 预算；审计只加枚举不写行；
- `SCHEMA`/`API.md`（Task 1/4 已改，补齐）；`AGENTS` §10.8 补"局部重识别"锚点：
  `ocr/refine.py` + `services/reocr_service.py` + `reocr_task` + `bbox_to_original`；
- `HANDOFF`：现状改"S3-3 已通，下一步 S3-4"，§7 换 S3-4 入口（OcrResult/JSON Schema 导出 + ocr_lines/invoice_fields
  落库契约；`GET /invoices/{id}/fields` 未实现）；
- spec/plan 状态改"已实现"。

- [ ] **Step 3: 提交**

```bash
git add docs AGENTS.md
git commit -m "docs(s3-3): 重识别口径、坐标修正与实测结果收口"
```

---

## 自检（对照 spec）

| spec 条目 | 覆盖 Task |
|---|---|
| §1 产出 1–10 | Task 1–7 |
| §2 四处裁决 | Task 1（Q3）、Task 3（Q4）、Task 4（Q1）、Task 5（Q2） |
| §3 reocr 契约（校验/错误码/备注） | Task 4 + Task 1 API.md |
| §4 refine 原语与坐标口径 | Task 2 |
| §5 submit/run/任务/队列 | Task 4、Task 5 |
| §6 bbox 坐标修正 | Task 3 |
| §7 审计枚举 + 0005 | Task 1 |
| §8 前端 | Task 6 |
| §9 测试 | Task 2/3/4/5 各自测试节 + Task 7 |
| §10 验收命令 | Task 7、Task 8 |
| §11 七处冲突 | C1 Task4、C2 Task5、C3 Task2、C4 Task1、C5 Task3、C6 Task4/8、C7 Task5/8 |
| §12 收口 | Task 8 |

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 0005 需要回退 | 无 `field_reread` 行时 `alembic downgrade 0004`；有行则迁移会拒绝（审计不可删） |
| 裁剪重识别提升不明显 | 调 `REREAD_PAD_*`/`REREAD_SCALE` 或把 `REREAD_BUDGET_SECONDS` 调大；如实写进 PROGRESS/论文 |
| 坐标修正影响旧数据 | 只影响新识别；本地旧测试票没有字段行；生产若有旧数据需一次性重跑识别（记在 HANDOFF） |
| 重识别接口被滥用 | 字段数 ≤6、已有进行中作业 409、单张 1.5s 预算；必要时加每用户限流（S7） |
| worker 未起 | 提交后 job 停在 `pending`；起 worker 后执行；`ocr-status` 能看到这次作业 |