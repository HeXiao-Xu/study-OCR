# S3-2 置信度聚合与确认队列实现计划（plan）

> 对应 spec：[`docs/spec/s3-2-confidence.md`](../spec/s3-2-confidence.md)（已确认）｜
> 契约：`docs/SCHEMA.md` §3.2/§3.3、`docs/API.md` §2 ｜ 状态：**已实现（2026-11-20）**，Task 1–7 全部执行并验证（见 `docs/PROGRESS.md` S3-2 行）
> 执行方式：Task 1→7 顺序；每个 Task 跑完对应验证命令再进下一个。

**目标**：`services/confidence.py` 统一字段置信度（0.90/0.60 三档 + 校验封顶 0.45），
流水线写 `need_review/overall_confidence/extract_status`，`/review` 成为 reviewer 队列。

**架构**：置信度公式与阈值只在 `confidence.py`（纯函数，`Settings` 注入）；抽取器只负责收集证据
（锚点行/值行 score、agreement、规则强度）并调用它；流水线负责聚合与落库；接口只派生 `review_level`。

**技术栈**：Python 3.11 / SQLAlchemy 2.0 / Pydantic v2 / Vue 3 + TS（无新增依赖）。

## Global Constraints

- 命令用 `.venv/Scripts/python.exe`；默认工作目录 `E:\BiSheXZY\study-OCR\backend`。
- **不改**已提交迁移；本卡**不动表结构**（`invoice_fields.confidence/need_review`、`invoices.overall_confidence` 都是既有列）。
- 金额 `Decimal`；置信度 `NUMERIC(4,3)`，一律 `round(x, 3)`。
- 阈值/权重只从 `core/config.py` 读；其它模块禁止 `os.environ`。
- 测试不得真连模型/MinIO/Redis（`-m integration` 用临时库 + 假引擎）。
- 文档同步：`SCHEMA`（公式/阈值/状态语义）、`API.md`（`review_level`、队列）、`.env.example`、`PROGRESS`、`DECISIONS`。
- 提交 Conventional Commits（`feat(s3-2): ...`）；**push 由用户执行**。

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/services/confidence.py`（新） | 公式、封顶、三档、整单聚合（纯函数） |
| `backend/app/core/config.py` + `.env.example` | 5 个置信度配置 + 校验 |
| `backend/app/ocr/types.py` | `ExtractedField` 增 `anchor_score/value_score/agreement` |
| `backend/app/ocr/extractor.py` | 记录证据成分、调用置信度公式、校验封顶 |
| `backend/app/services/ocr_pipeline.py` | 聚合与写回（`need_review`/`overall_confidence`/`extract_status`/metrics） |
| `backend/app/schemas/invoice.py` + `services/invoice_service.py` | 派生 `review_level` |
| `backend/tests/{test_confidence,test_extractor,test_ocr_pipeline,test_invoices,test_config}.py` | 单元 + 集成 |
| `web/src/{router/index.ts,utils/landing.ts,views/InvoiceDetail.vue,types/invoice.ts}` | 队列与阈值对齐 |
| `scripts/confidence_eval.py`（新） | 40 张阈值扫描与不变量验收 |

---

### Task 1: 契约 + 配置 + `confidence.py`

**Files:**
- Modify: `docs/SCHEMA.md` §3.2 / §3.3
- Modify: `backend/app/core/config.py`、`backend/.env.example`
- Create: `backend/app/services/confidence.py`
- Test: `backend/tests/test_confidence.py`（新）、`backend/tests/test_config.py`

**Interfaces:**
- Produces（后续 Task 依赖，签名固定）：
  ```python
  ReviewLevel = Literal["auto", "review", "manual"]

  @dataclass(frozen=True)
  class FieldScore: field_name: str; score: float; conflict: bool = False; validator_failed: bool = False
  @dataclass(frozen=True)
  class FieldConfidence: field_name: str; score: float; level: ReviewLevel
  @dataclass(frozen=True)
  class InvoiceConfidence: overall: float; needs_review: list[str]; levels: dict[str, str]; counts: dict[str, int]

  def compute_field_confidence(anchor_score: float, value_score: float, *, agreement: float = 1.0,
                               rule_factor: float = 1.0, settings: Settings | None = None) -> float
  def apply_validator_cap(score: float, *, settings: Settings | None = None) -> float
  def level_for(score: float, *, settings: Settings | None = None) -> ReviewLevel
  def resolve_field(item: FieldScore, *, settings: Settings | None = None) -> FieldConfidence
  def aggregate_invoice(items: list[FieldConfidence]) -> InvoiceConfidence
  ```

- [ ] **Step 1: 先改 SCHEMA（契约先行）**

`docs/SCHEMA.md` §3.2 `overall_confidence` 说明改为：

```markdown
| overall_confidence | NUMERIC(4,3) | 是 | — | 整单置信度 = **已抽到字段置信度的算术平均**（无字段记 0）；**只用于展示/排序，不参与单字段是否核对的判定**（S3-2/D-058）✅ | CHECK 0–1 ⚠ |
```

§3.3 `confidence` / `need_review` 两行说明改为：

```markdown
| confidence | NUMERIC(4,3) | 是 | — | 字段置信度 = `(0.35×锚点行score + 0.65×值行score) × agreement × rule_factor`（权重与公式见 `services/confidence.py`）；`validators` 判错封顶 **0.45** ✅ | `CHECK BETWEEN 0 AND 1` ✅ |
| need_review | BOOLEAN | 否 | `false` | 是否需要人工确认：置信度 **< 0.90**（`review`）或 **< 0.60**（`manual`）或存在未决冲突（S3-2/D-058）✅ | idx ⚠ |
```

并在 §3.3 说明段补一句：

```markdown
> 三档口径（S3-2，D-058）：`auto ≥0.90` 免人工、`review 0.60–0.90` 待核对、`manual <0.60` 必须人工；
> `need_review = level != auto`。`extract_status`：全部 `auto` 保持 `raw`；有 `review/manual`、缺必填或冲突 → `need_confirm`；
> `confirmed` 只由人工确认动作（S3-6）设置——"机器自动通过"不等于"人已确认"。
```

- [ ] **Step 2: 写失败测试**

`tests/test_confidence.py`：

```python
"""S3-2 置信度聚合：公式、封顶、三档、整单聚合。

阈值与权重全部通过显式 Settings 传入，测试不读 .env（CI/本地一致）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.confidence import (
    FieldConfidence,
    FieldScore,
    aggregate_invoice,
    apply_validator_cap,
    compute_field_confidence,
    level_for,
    resolve_field,
)

_BASE = dict(
    database_url="postgresql://u:p@127.0.0.1:5433/db",
    redis_url="redis://:p@127.0.0.1:6380/0",
    minio_endpoint="127.0.0.1:9002", minio_access_key="ak", minio_secret_key="sk",
    minio_bucket="bucket", jwt_secret="s" * 32,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_BASE, **overrides})


def test_compute_field_confidence_is_weighted_base_times_factors() -> None:
    settings = _settings()
    # 0.35*0.8 + 0.65*0.9 = 0.865
    assert compute_field_confidence(0.8, 0.9, settings=settings) == pytest.approx(0.865)
    # × agreement 0.85 × rule_factor 0.84
    assert compute_field_confidence(0.8, 0.9, agreement=0.85, rule_factor=0.84,
                                    settings=settings) == pytest.approx(0.617)


def test_compute_field_confidence_clamps_to_unit_interval() -> None:
    settings = _settings(confidence_weight_anchor=1.0, confidence_weight_value=0.0)
    assert compute_field_confidence(1.0, 1.0, agreement=1.2, settings=settings) == 1.0
    assert compute_field_confidence(0.0, 0.0, settings=settings) == 0.0


def test_level_for_boundaries() -> None:
    settings = _settings()
    assert level_for(0.90, settings=settings) == "auto"
    assert level_for(0.899, settings=settings) == "review"
    assert level_for(0.60, settings=settings) == "review"
    assert level_for(0.599, settings=settings) == "manual"


def test_validator_cap_pins_failed_field_below_half() -> None:
    settings = _settings()
    assert apply_validator_cap(0.92, settings=settings) == 0.45
    assert apply_validator_cap(0.30, settings=settings) == 0.30      # 不反弹


def test_resolve_field_forces_conflict_into_review() -> None:
    settings = _settings()
    resolved = resolve_field(FieldScore("total_amount", 0.97, conflict=True), settings=settings)
    assert resolved.level == "review" and resolved.score == 0.97

    failed = resolve_field(FieldScore("total_amount", 0.97, validator_failed=True), settings=settings)
    assert failed.score == 0.45 and failed.level == "manual"


def test_aggregate_invoice_mean_and_levels() -> None:
    items = [
        FieldConfidence("invoice_no", 0.97, "auto"),
        FieldConfidence("total_amount", 0.75, "review"),
        FieldConfidence("buyer_name", 0.40, "manual"),
    ]
    result = aggregate_invoice(items)
    assert result.overall == pytest.approx(0.707)          # (0.97+0.75+0.40)/3 = 0.7067
    assert result.needs_review == ["total_amount", "buyer_name"]
    assert result.counts == {"auto": 1, "review": 1, "manual": 1}
    assert result.levels["buyer_name"] == "manual"


def test_aggregate_invoice_empty_is_zero() -> None:
    result = aggregate_invoice([])
    assert result.overall == 0.0
    assert result.needs_review == [] and result.counts == {"auto": 0, "review": 0, "manual": 0}


@pytest.mark.parametrize(
    "overrides",
    [
        {"confidence_weight_anchor": 0.5, "confidence_weight_value": 0.2},   # 和 ≠ 1
        {"confidence_weight_anchor": -0.1, "confidence_weight_value": 1.1},  # 越界
        {"confidence_auto_confirm": 0.5},                                     # auto <= manual
        {"confidence_validator_cap": 0.8},                                    # 封顶必须 < manual 阈值
    ],
)
def test_confidence_settings_are_validated(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)
```

`tests/test_config.py` 追加：

```python
def test_confidence_settings_defaults() -> None:
    settings = _settings()
    assert (settings.confidence_auto_confirm, settings.confidence_manual_below) == (0.90, 0.60)
    assert settings.confidence_validator_cap == 0.45
    assert settings.confidence_weight_anchor + settings.confidence_weight_value == pytest.approx(1.0)
```

- [ ] **Step 3: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_confidence.py tests/test_config.py
```

预期：`ModuleNotFoundError: app.services.confidence` / 缺少配置项。

- [ ] **Step 4: 实现配置**

`core/config.py` OCR 段后追加：

```python
    # ---------- 置信度聚合（S3-2）----------
    # 三档口径：>=auto 免人工；[manual, auto) 待核对；<manual 必须人工（D-058）
    confidence_auto_confirm: float = 0.90
    confidence_manual_below: float = 0.60
    # validators 判错的字段封顶到该值（任务卡"直接 <0.5"）
    confidence_validator_cap: float = 0.45
    # 基础分 = 锚点行 score × w_anchor + 值行 score × w_value（权重和必须为 1）
    confidence_weight_anchor: float = 0.35
    confidence_weight_value: float = 0.65
```

```python
    @model_validator(mode="after")
    def _check_confidence_config(self) -> "Settings":
        weights = (self.confidence_weight_anchor, self.confidence_weight_value)
        if not all(0.0 <= weight <= 1.0 for weight in weights):
            raise ValueError("置信度权重必须在 [0,1]")
        if abs(sum(weights) - 1.0) > 1e-6:
            raise ValueError("置信度权重之和必须为 1")
        if not 0.0 <= self.confidence_validator_cap < self.confidence_manual_below:
            raise ValueError("CONFIDENCE_VALIDATOR_CAP 必须小于 CONFIDENCE_MANUAL_BELOW")
        if not 0.0 <= self.confidence_manual_below < self.confidence_auto_confirm <= 1.0:
            raise ValueError("置信度阈值必须满足 0 <= manual < auto <= 1")
        return self
```

`.env.example` 追加：

```dotenv
# ---------- 置信度聚合（S3-2）----------
# 三档：>=AUTO 免人工；[MANUAL, AUTO) 待核对；<MANUAL 必须人工
CONFIDENCE_AUTO_CONFIRM=0.90
CONFIDENCE_MANUAL_BELOW=0.60
# validators 判错的字段置信度封顶（任务卡要求 <0.5）
CONFIDENCE_VALIDATOR_CAP=0.45
# 基础分权重：锚点行 score / 值行 score，两者之和必须为 1
CONFIDENCE_WEIGHT_ANCHOR=0.35
CONFIDENCE_WEIGHT_VALUE=0.65
```

- [ ] **Step 5: 实现 `confidence.py`**

```python
"""字段级/整单置信度聚合（S3-2）。

为什么单独一个模块：置信度是"要不要人工看"的唯一判据，公式与阈值必须可配置、可单测、
只有一个实现。抽取器只负责把证据（锚点行 score / 值行 score / agreement / 规则强度）带出来，
不自己决定三档。

公式（D-058）：
    ocr_base = w_anchor × anchor_score + w_value × value_score     # 默认 0.35 / 0.65
    raw      = ocr_base × agreement × rule_factor                  # 都是 [0,1]
    score    = round(clamp(raw, 0, 1), 3)
    validators 判错 → min(score, validator_cap=0.45)
    level    = auto (>=0.90) / review (>=0.60) / manual
    冲突字段（同优先级多候选）不允许 auto，降为 review —— 必须人看候选。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.core.config import Settings, get_settings

__all__ = [
    "AUTO",
    "MANUAL",
    "REVIEW",
    "FieldConfidence",
    "FieldScore",
    "InvoiceConfidence",
    "ReviewLevel",
    "aggregate_invoice",
    "apply_validator_cap",
    "compute_field_confidence",
    "level_for",
    "resolve_field",
]

ReviewLevel = Literal["auto", "review", "manual"]
AUTO: ReviewLevel = "auto"
REVIEW: ReviewLevel = "review"
MANUAL: ReviewLevel = "manual"


def _settings(settings: Settings | None) -> Settings:
    return settings or get_settings()


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


@dataclass(frozen=True)
class FieldScore:
    field_name: str
    score: float
    conflict: bool = False
    validator_failed: bool = False


@dataclass(frozen=True)
class FieldConfidence:
    field_name: str
    score: float
    level: ReviewLevel


@dataclass(frozen=True)
class InvoiceConfidence:
    overall: float
    needs_review: list[str] = field(default_factory=list)
    levels: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {"auto": 0, "review": 0, "manual": 0})


def compute_field_confidence(anchor_score: float, value_score: float, *, agreement: float = 1.0,
                             rule_factor: float = 1.0, settings: Settings | None = None) -> float:
    conf = _settings(settings)
    base = (conf.confidence_weight_anchor * float(anchor_score)
            + conf.confidence_weight_value * float(value_score))
    return round(_clamp(base * float(agreement) * float(rule_factor)), 3)


def apply_validator_cap(score: float, *, settings: Settings | None = None) -> float:
    cap = _settings(settings).confidence_validator_cap
    return round(min(float(score), cap), 3)


def level_for(score: float, *, settings: Settings | None = None) -> ReviewLevel:
    conf = _settings(settings)
    if score >= conf.confidence_auto_confirm:
        return AUTO
    if score >= conf.confidence_manual_below:
        return REVIEW
    return MANUAL


def resolve_field(item: FieldScore, *, settings: Settings | None = None) -> FieldConfidence:
    score = apply_validator_cap(item.score, settings=settings) if item.validator_failed else round(item.score, 3)
    level = level_for(score, settings=settings)
    if item.conflict and level == AUTO:
        level = REVIEW
    return FieldConfidence(field_name=item.field_name, score=score, level=level)


def aggregate_invoice(items: list[FieldConfidence]) -> InvoiceConfidence:
    if not items:
        return InvoiceConfidence(overall=0.0)
    counts = {"auto": 0, "review": 0, "manual": 0}
    for item in items:
        counts[item.level] += 1
    needs = sorted(item.field_name for item in items if item.level != AUTO)
    overall = round(sum(item.score for item in items) / len(items), 3)
    return InvoiceConfidence(
        overall=overall,
        needs_review=needs,
        levels={item.field_name: item.level for item in items},
        counts=counts,
    )
```

- [ ] **Step 6: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_confidence.py tests/test_config.py
```

- [ ] **Step 7: 提交**

```bash
git add docs/SCHEMA.md backend/app/core/config.py backend/.env.example \
  backend/app/services/confidence.py backend/tests/test_confidence.py backend/tests/test_config.py
git commit -m "feat(s3-2): 置信度聚合服务与三档阈值配置"
```

---

### Task 2: `ExtractedField` 成分 + 抽取器接公式

**Files:**
- Modify: `backend/app/ocr/types.py`、`backend/app/ocr/extractor.py`
- Test: `backend/tests/test_extractor.py`、`backend/tests/test_validators.py`

**Interfaces:**
- Produces: `ExtractedField.anchor_score: float | None`、`value_score: float | None`、`agreement: float`

- [ ] **Step 1: 改测试（失败）**

`tests/test_extractor.py` 把 `test_invalid_uscc_halves_confidence` 改为封顶口径：

```python
def test_invalid_uscc_caps_confidence_below_half() -> None:
    broken = VALID_USCC[:10] + ("0" if VALID_USCC[10] != "0" else "1") + VALID_USCC[11:]
    lines = [
        _line("发票号码：12345678", 90, 0),
        _line("开票日期：2025年11月04日", 120, 1),
        _line("价税合计：¥100.00", 300, 2),
        _line(f"纳税人识别号：{broken}", 330, 3),
    ]
    result = extract_fields(lines, (1200, 800), "vat_general")
    field = result.fields["buyer_tax_no"]
    assert field.confidence <= 0.45                 # 校验失败封顶（任务卡"直接 <0.5"）
    assert any("invalid_uscc" in warning for warning in result.warnings)


def test_anchor_hit_records_confidence_components() -> None:
    from app.services.confidence import level_for

    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    field = result.fields["invoice_no"]
    assert field.anchor_score is not None and field.value_score is not None
    assert field.agreement == 1.0
    assert field.confidence == round(field.confidence, 3)
    assert level_for(field.confidence) in {"auto", "review", "manual"}


def test_derived_field_has_no_ocr_components() -> None:
    lines = [
        _line("电子发票（普通发票）", 50, 0),
        _line("发票号码：12345678", 90, 1),
        _line("开票日期：2025年11月04日", 120, 2),
        _line("合计  ¥184.80  ¥11.09", 500, 3),
    ]
    result = extract_fields(lines, (1200, 800), "vat_digital")
    total = result.fields.get("total_amount")
    if total is not None and total.rule_id.startswith("derived:"):
        assert total.anchor_score is None and total.value_score is None
```

`tests/test_validators.py` 把误名用例改名（不改断言）：

```python
def test_run_validators_reports_failed_fields() -> None:   # 原 test_run_validators_halves_confidence_for_failed_fields
```

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_extractor.py -k "components or caps"
```

- [ ] **Step 3: 实现**

`ocr/types.py` `ExtractedField` 增（`rule_strength` 之后）：

```python
    # 置信度成分（S3-2）：锚点行 / 值行的 OCR score 与取值一致性，供 confidence.py 复算与追溯
    anchor_score: float | None = Field(default=None, ge=0.0, le=1.0)
    value_score: float | None = Field(default=None, ge=0.0, le=1.0)
    agreement: float = Field(default=1.0, ge=0.0, le=1.0)
```

`ocr/extractor.py`：

1. 顶部 `from app.services.confidence import apply_validator_cap, compute_field_confidence`
   —— 注意 `confidence` 只依赖 `core.config`，不会与 `ocr/` 形成环。
2. 锚点命中构造处（原 `confidence=round(min(1.0, source_line.score * strength), 4)`）改为：
   取值分支要先记"是不是跨行取的"（`value is None` 在 append 时已不成立）：

```python
                value = _extract_value(rule, anchor_line.text, anchor)
                source_line = anchor_line
                strength = base_strength
                used_neighbour = False
                if value is None:
                    neighbour = _neighbour_value(rule, anchor_line, ordered, image_height)
                    if neighbour is None:
                        continue
                    value, source_line_no = neighbour
                    source_line = next((line for line in ordered if line.line_no == source_line_no), anchor_line)
                    strength = base_strength * _FUZZY_PENALTY
                    used_neighbour = True
                agreement = _FUZZY_PENALTY if used_neighbour else 1.0
                hits.append(ExtractedField(
                    name=rule.name,
                    value=value,
                    raw_text=source_line.text,
                    source_line_no=source_line.line_no,
                    confidence=compute_field_confidence(
                        anchor_line.score, source_line.score,
                        agreement=agreement, rule_factor=base_strength,
                    ),
                    rule_id=rule.rule_id,
                    rule_strength=round(strength, 3),
                    bbox=source_line.bbox(),
                    anchor_score=anchor_line.score,
                    value_score=source_line.score,
                    agreement=agreement,
                ))
```

3. 校验汇总（文件末尾原"折半"）改为：

```python
    validation = run_validators(result.fields, ticket_type)
    for issue in validation.issues:
        if issue.severity == "error" and issue.field in result.fields:
            field = result.fields[issue.field]
            field.confidence = apply_validator_cap(field.confidence)
```

（`paired:sum:*` / `derived:*` 两条构造路径保持原置信度 0.6 / 0.55，`anchor_score/value_score` 为 `None`。）

- [ ] **Step 4: 验证（含全量回归）**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_extractor.py tests/test_validators.py
../.venv/Scripts/python.exe -m pytest -q
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/ocr/types.py backend/app/ocr/extractor.py backend/tests/test_extractor.py backend/tests/test_validators.py
git commit -m "feat(s3-2): 抽取器记录置信度成分并改用统一公式（校验失败封顶 0.45）"
```

---

### Task 3: 流水线写回 `need_review` / `overall_confidence` / `extract_status`

**Files:**
- Modify: `backend/app/services/ocr_pipeline.py`
- Test: `backend/tests/test_ocr_pipeline.py`

**Interfaces:**
- `field_rows(merged, *, invoice_id, rule_ids, confidences: dict[str, FieldConfidence])`
- `_persist(..., confidences, missing_required, has_conflict, ...)` 写 `ocr_status` 之外的置信度与抽取状态

- [ ] **Step 1: 改/加测试（失败）**

单元（`tests/test_ocr_pipeline.py` 的 `field_rows` 用例改签名）：

```python
def test_field_rows_uses_resolved_confidence_for_need_review() -> None:
    from app.services.confidence import FieldConfidence

    merged = {
        "total_amount": MergedField(_field("total_amount", "100.00", confidence=0.70), 1),
        "buyer_name": MergedField(_field("buyer_name", "张三", confidence=0.97), 1),
    }
    rows = field_rows(
        merged, invoice_id=5, rule_ids={"total_amount": 11},
        confidences={
            "total_amount": FieldConfidence("total_amount", 0.70, "review"),
            "buyer_name": FieldConfidence("buyer_name", 0.97, "auto"),
        },
    )
    by_name = {row["field_name"]: row for row in rows}
    assert by_name["total_amount"]["need_review"] is True
    assert by_name["total_amount"]["confidence"] == Decimal("0.700")
    assert by_name["buyer_name"]["need_review"] is False
    assert by_name["buyer_name"]["source"] == "ocr"
```

集成（追加）：

```python
@pytest.mark.integration
def test_run_pipeline_writes_review_levels_and_overall(session_factory, owner) -> None:
    # 票价合计正常 → auto；缺 invoice_date（必填）→ 整单 need_confirm
    lines = [
        _line("电子发票（普通发票）", 0, y=10),
        _line("发票号码：12345678", 1, y=120),
        _line("价税合计（小写）￥99.00", 2, y=400),
    ]
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    outcome, _ = _execute(session_factory, invoice_id, _deps(FakeEngine({0: lines})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        job = db.get(OcrJob, job_id)
        assert invoice.extract_status == "need_confirm"       # 缺必填
        assert invoice.overall_confidence is not None
        assert job.metrics["counts"]["review_levels"]["auto"] >= 1
        rows = {row.field_name: row for row in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice_id))}
        assert rows["total_amount"].need_review is False


@pytest.mark.integration
def test_run_pipeline_conflict_forces_review(session_factory, owner) -> None:
    # 两条同锚点、不同金额 → candidates>1（冲突必须人工）
    lines = [
        _line("增值税电子普通发票", 0, y=10),
        _line("价税合计（小写）￥100.00", 1, y=300),
        _line("价税合计（小写）￥200.00", 2, y=340),
    ]
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    outcome, _ = _execute(session_factory, invoice_id, _deps(FakeEngine({0: lines})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        row = db.scalar(select(InvoiceField).where(
            InvoiceField.invoice_id == invoice_id, InvoiceField.field_name == "total_amount"))
        assert row.conflict is True and row.need_review is True


@pytest.mark.integration
def test_run_pipeline_extract_status_matches_needs(session_factory, owner) -> None:
    """不写死"一定是 raw/need_confirm"，而是断言状态与字段实际需要**一致**（回归时不会被阈值变动打脸）。"""
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    outcome, _ = _execute(session_factory, invoice_id,
                          _deps(FakeEngine({0: _vat_digital_lines_full()})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        job = db.get(OcrJob, job_id)
        rows = db.scalars(select(InvoiceField).where(InvoiceField.invoice_id == invoice_id)).all()
        needs = any(row.need_review for row in rows) or bool(job.metrics["counts"]["missing_required"])
        assert invoice.extract_status == ("need_confirm" if needs else "raw")
        assert invoice.overall_confidence == pytest.approx(job.metrics["counts"]["overall_confidence"], abs=1e-3)
```

helper（追加到集成区）：

```python
def _vat_digital_lines_full() -> list[OcrLineModel]:
    return [
        _line("电子发票（普通发票） 全电发票 数电票", 0, y=10),
        _line("发票号码：12345678", 1, y=120),
        _line("开票日期：2026-03-01", 2, y=160),
        _line("合计  ¥100.00  ¥13.00", 3, y=380),
        _line("价税合计（小写）￥113.00", 4, y=420),
    ]
```

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest tests/test_ocr_pipeline.py -k "resolved_confidence"
../.venv/Scripts/python.exe -m pytest -m integration tests/test_ocr_pipeline.py -k "review_levels or forces_review or all_auto"
```

- [ ] **Step 3: 实现**

`ocr_pipeline.py`：

1. 删 `NEED_REVIEW_THRESHOLD`，改从 `confidence` 引入：

```python
from app.services.confidence import (
    FieldConfidence,
    FieldScore,
    aggregate_invoice,
    resolve_field,
)
```

2. `field_rows` 签名与实现改：

```python
def field_rows(
    merged: dict[str, MergedField],
    *,
    invoice_id: int,
    rule_ids: dict[str, int],
    confidences: dict[str, FieldConfidence],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, item in merged.items():
        extracted = item.field
        resolved = confidences.get(name)
        level = resolved.level if resolved is not None else "review"
        score = resolved.score if resolved is not None else round(float(extracted.confidence), 3)
        has_conflict = len(extracted.candidates) > 1
        rows.append({
            ...
            "confidence": Decimal(str(score)),
            ...
            "need_review": level != "auto",
            "conflict": has_conflict,
            "conflict_value": extracted.candidates[1] if has_conflict else None,
        })
    return rows
```

3. `run_pipeline` 在 `_extract` 后计算：

```python
        scores = [
            resolve_field(FieldScore(
                field_name=name,
                score=float(item.field.confidence),
                conflict=len(item.field.candidates) > 1,
                validator_failed=name in failed_fields,
            ))
            for name, item in merged.items()
        ]
        confidences = {item.field_name: item for item in scores}
        invoice_confidence = aggregate_invoice(scores)
        metrics["counts"]["needs_review"] = len(invoice_confidence.needs_review)
        metrics["counts"]["review_levels"] = invoice_confidence.counts
        metrics["counts"]["overall_confidence"] = invoice_confidence.overall
```

4. `_persist` 增参数 `confidences: dict[str, FieldConfidence]`、`invoice_confidence: InvoiceConfidence`、
   `missing_required: list[str]`、`has_conflict: bool`；写 `invoice_fields` 时传 `confidences`；
   invoices 更新值里加：

```python
    need_confirm = bool(invoice_confidence.needs_review) or bool(missing_required) or has_conflict
    final = {
        "ocr_status": OcrStatus.SUCCEEDED.value,
        "overall_confidence": invoice_confidence.overall,
        "extract_status": (ExtractStatus.NEED_CONFIRM.value if need_confirm
                           else ExtractStatus.RAW.value),
        **({"quality_flag": quality_flag} if quality_flag in ("ok", "low") else {}),
    }
```

（`ExtractStatus` 加入 `models.enums` 导入。）

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_pipeline.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_ocr_pipeline.py
../.venv/Scripts/python.exe -m pytest -q          # 全量不回归
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/ocr_pipeline.py backend/tests/test_ocr_pipeline.py
git commit -m "feat(s3-2): 流水线写回 need_review/overall_confidence/extract_status"
```

---

### Task 4: 详情字段派生 `review_level`

**Files:**
- Modify: `backend/app/schemas/invoice.py`、`backend/app/services/invoice_service.py`
- Modify: `docs/API.md` §2（详情 / fields 示例）
- Test: `backend/tests/test_invoices.py`

**Interfaces:**
- Produces: `InvoiceFieldOut.review_level: "auto"|"review"|"manual"|None`（不落库，服务端派生）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_invoices.py` 的详情区，用现有 `actors/client/_make_invoice/_seed_field`）

```python
def test_detail_field_exposes_review_level(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        db.add(InvoiceField(invoice_id=invoice_id, field_name="total_amount",
                            field_value="100.00", normalized_value="100.00",
                            source="ocr", confidence=Decimal("0.700")))
        db.add(InvoiceField(invoice_id=invoice_id, field_name="buyer_name",
                            field_value="张三", normalized_value="张三",
                            source="ocr", confidence=Decimal("0.950")))
        db.commit()

    data = client.get(f"{BASE}/{invoice_id}", headers=_auth(actors["alice"])).json()["data"]
    levels = {row["field_name"]: row["review_level"] for row in data["fields"]}
    assert levels == {"buyer_name": "auto", "total_amount": "review"}
```

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_invoices.py -k review_level
```

- [ ] **Step 3: 实现**

`schemas/invoice.py` `InvoiceFieldOut` 增：

```python
    # 派生字段（不落库）：auto/review/manual，用来替代前端硬编码阈值（S3-2）
    review_level: Literal["auto", "review", "manual"] | None = None
```

`invoice_service._field_out` 增：

```python
from app.services.confidence import level_for   # 文件顶部

def _field_out(field: InvoiceField) -> InvoiceFieldOut:
    review_level = None
    if field.confidence is not None:
        review_level = level_for(float(field.confidence))
    return InvoiceFieldOut(..., review_level=review_level)
```

`docs/API.md` §2 详情出参示例的字段对象补 `"review_level": "auto"`，备注加一句：

```markdown
- `fields[].review_level` 为服务端派生（`auto`≥0.90 / `review` 0.60–0.90 / `manual`<0.60），不落库；
  前端颜色与文案以它为准，不要自己硬编码阈值（S3-2/D-058）。
```

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_invoices.py
../.venv/Scripts/python.exe -m pytest -q tests/test_api_docs.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas/invoice.py backend/app/services/invoice_service.py docs/API.md backend/tests/test_invoices.py
git commit -m "feat(s3-2): 详情字段输出派生 review_level"
```

---

### Task 5: 前端队列与阈值对齐

**Files:**
- Modify: `web/src/router/index.ts`、`web/src/utils/landing.ts`、`web/src/types/invoice.ts`、`web/src/views/InvoiceDetail.vue`

- [ ] **Step 1: 路由与落地**

`router/index.ts`：把 `/review` 的 Placeholder 路由换成重定向（保留 `meta.roles` 不可行——redirect 无组件，改为在 `beforeEach` 之外直接）：

```ts
      {
        path: 'review',
        redirect: '/invoices?extract_status=need_confirm',
      },
```

`utils/landing.ts`：

```ts
export function landingFor(role: UserRole | null): string {
  if (role === 'admin') return '/templates'
  if (role === 'reviewer') return '/review'
  return '/invoices'
}
```

- [ ] **Step 2: 类型与详情页**

`types/invoice.ts` `InvoiceField` 增：

```ts
  review_level?: 'auto' | 'review' | 'manual' | null
```

`InvoiceDetail.vue`：

```ts
function confidenceTag(row: InvoiceField): 'success' | 'warning' | 'danger' | 'info' {
  if (row.review_level === 'manual') return 'danger'
  if (row.review_level === 'review') return 'warning'
  if (row.review_level === 'auto') return 'success'
  const number = confidenceNumber(row.confidence)
  if (number === null) return 'info'
  if (number < 0.6) return 'danger'
  if (number < 0.9) return 'warning'
  return 'success'
}
```

模板里 `:type="confidenceTag(row)"` 改传整行（原 `confidenceTag(row.confidence)`），
卡片副标题改为 `置信度低于 90% 标黄、低于 60% 标红并必须人工核对；冲突字段不自动覆盖`。

- [ ] **Step 3: 验证**

```bash
cd /e/BiSheXZY/study-OCR/web && npm run typecheck && npm run build
```

reviewer 登录冒烟（后端 + Vite + worker 在跑；本地 reviewer 口令未知，按 S3-1 的做法临时建一个）：

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe ../scripts/create_user.py --username s32review --role reviewer \
  --real-name S32验收 --password 'S32review!2026'
node ../scripts/ui_smoke.mjs "http://127.0.0.1:5173/login" --clear --view 1440x900 \
  --fill 'input[autocomplete="username"]' s32review \
  --fill 'input[autocomplete="current-password"]' 'S32review!2026' \
  --click 登录 --expect 待核对 --shot /tmp/s32-review.png
```

冒烟后删掉临时账号（不留带已知口令的账号）：

```bash
../.venv/Scripts/python.exe ../scripts/delete_test_user.py --username s32review
```

（若尚无该脚本，用 SQL 删：`delete from users where username='s32review';`——本卡不新增脚本，
执行者按现有惯例在 python 里 `get_session_factory()` 执行即可。）

- [ ] **Step 4: 提交**

```bash
git add web/src/router/index.ts web/src/utils/landing.ts web/src/types/invoice.ts web/src/views/InvoiceDetail.vue
git commit -m "feat(s3-2): reviewer 队列接入 /review，详情置信度按 review_level 对齐"
```

---

### Task 6: 验收脚本与 40 张实测

**Files:**
- Create: `scripts/confidence_eval.py`

- [ ] **Step 1: 写脚本**（离线，不写库；与 `scripts/ocr_baseline.py` 同样的 `sys.path` 处理）

```python
"""S3-2 置信度验收：N 张合成票据离线跑（预处理→识别→抽取→置信度）。

用法：
    .venv/Scripts/python.exe scripts/confidence_eval.py --samples 40
不变量（任一不成立 → 退出码 1）：
1. 阈值扫描：字段需人工数 / 整单需核对数随阈值单调不减；
2. validators 判错的字段 score <= 0.45（任务卡"直接 <0.5"）；
3. level=auto 的字段 score >= 0.90；level=review 的字段要么 <0.90、要么是冲突字段；
4. 整单 needs_review 与字段 level 一一对应。
结果写 backend/eval/results/confidence_eval_<ts>.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)          # Settings 从 backend/.env 读，和其他脚本一致

from app.core.config import get_settings  # noqa: E402
from app.ocr.detector import OcrOptions, get_engine  # noqa: E402
from app.ocr.extractor import extract_fields, load_field_rules  # noqa: E402
from app.ocr.intake import prepare_pages  # noqa: E402
from app.ocr.layout import analyze_layout, load_templates  # noqa: E402
from app.ocr.validators import run_validators  # noqa: E402
from app.services.confidence import (  # noqa: E402
    FieldScore,
    aggregate_invoice,
    resolve_field,
)

SAMPLE_DIR = ROOT / "samples" / "synth_v1"
RESULT_DIR = BACKEND / "eval" / "results"
THRESHOLDS = (0.50, 0.60, 0.70, 0.80, 0.90, 0.95)


def _collect(samples, engine, rules, templates) -> list[dict]:
    records: list[dict] = []
    for path in samples:
        prepared = prepare_pages(path, max_pages=1)
        ocr = engine.recognize(prepared[0].image)
        layout = analyze_layout(ocr.lines, ocr.image_size, templates)
        extracted = extract_fields(ocr.lines, ocr.image_size, layout.ticket_type, layout, rules)
        failed = run_validators(extracted.fields, layout.ticket_type).failed_fields()

        resolved, flags = [], []
        for name, field in extracted.fields.items():
            has_conflict = len(field.candidates) > 1
            resolved.append(resolve_field(FieldScore(
                field_name=name, score=float(field.confidence),
                conflict=has_conflict, validator_failed=name in failed,
            )))
            flags.append({"name": name, "conflict": has_conflict,
                          "validator_failed": name in failed})

        invoice_conf = aggregate_invoice(resolved)
        records.append({
            "file": path.name,
            "ticket_type": layout.ticket_type,
            "missing_required": extracted.missing_required,
            "fields": [{"name": item.field_name, "score": item.score, "level": item.level}
                       for item in resolved],
            "flags": flags,
            "invoice": {"overall": invoice_conf.overall,
                        "needs_review": invoice_conf.needs_review,
                        "counts": invoice_conf.counts},
        })
    return records


def _sweep(records: list[dict], threshold: float) -> dict[str, int]:
    return {
        "fields": sum(1 for rec in records for field in rec["fields"]
                      if field["score"] < threshold),
        "invoices": sum(1 for rec in records
                        if any(field["score"] < threshold for field in rec["fields"])
                        or bool(rec["missing_required"])),
    }


def _problems(records: list[dict], sweep: dict[str, dict[str, int]]) -> list[str]:
    problems: list[str] = []
    for key in ("fields", "invoices"):
        series = [entry[key] for entry in sweep.values()]
        if series != sorted(series):
            problems.append(f"阈值扫描不单调：{key} = {series}")

    for rec in records:
        for field, flag in zip(rec["fields"], rec["flags"]):
            if flag["validator_failed"] and field["score"] > 0.45 + 1e-9:
                problems.append(f"{rec['file']}/{field['name']} 校验失败但 score={field['score']}")
            if field["level"] == "auto" and field["score"] < 0.90:
                problems.append(f"{rec['file']}/{field['name']} auto 但 score={field['score']}")
            if (field["level"] == "review" and field["score"] >= 0.90
                    and not flag["conflict"]):
                problems.append(f"{rec['file']}/{field['name']} review 但 score={field['score']} 且无冲突")
        expected = sorted(field["name"] for field in rec["fields"] if field["level"] != "auto")
        if rec["invoice"]["needs_review"] != expected:
            problems.append(f"{rec['file']} needs_review 与字段 level 不一致")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="S3-2 置信度验收")
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    samples = sorted(SAMPLE_DIR.glob("*.jpg"))[: args.samples]
    if len(samples) < args.samples:
        print(f"样例不足：需要 {args.samples} 张，实际 {len(samples)} 张", file=sys.stderr)
        return 2

    engine = get_engine(OcrOptions.from_settings(get_settings()))
    records = _collect(samples, engine, load_field_rules(), load_templates())
    sweep = {f"{threshold:.2f}": _sweep(records, threshold) for threshold in THRESHOLDS}
    problems = _problems(records, sweep)

    levels = Counter(field["level"] for rec in records for field in rec["fields"])
    scores = [field["score"] for rec in records for field in rec["fields"]]
    buckets = Counter(f"{(score // 0.1) / 10:.1f}" for score in scores)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(records),
        "fields": len(scores),
        "thresholds": sweep,
        "levels": dict(levels),
        "score_buckets": dict(sorted(buckets.items())),
        "problems": problems,
        "records": records,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or RESULT_DIR / f"confidence_eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"样本 {len(records)} 张 / 字段 {len(scores)} 个；三档分布 {dict(levels)}")
    print("阈值   字段需人工  整单需核对")
    for threshold, entry in sweep.items():
        print(f"{threshold}  {entry['fields']:>8}  {entry['invoices']:>10}")
    print("分数分布：" + "  ".join(f"{key}~{buckets[key]}" for key in sorted(buckets)))
    if problems:
        print(f"❌ 不变量失败 {len(problems)} 条：")
        for item in problems[:10]:
            print(f"  - {item}")
    else:
        print("✅ 不变量全过（单调性 + 校验封顶 + auto/review 口径）")
    print(f"报告：{out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 跑 40 张**

```bash
cd /e/BiSheXZY/study-OCR && .venv/Scripts/python.exe scripts/confidence_eval.py --samples 40
```

判定：阈值扫描单调不减；不变量全过；控制台输出三档分布。把实测表贴进 PROGRESS（Task 7）。

- [ ] **Step 3: 提交**

```bash
git add scripts/confidence_eval.py
git commit -m "feat(s3-2): 置信度验收脚本（40 张阈值扫描与不变量）"
```

---

### Task 7: 全量回归 + 文档收口

- [ ] **Step 1: 全量验证**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
```

- [ ] **Step 2: 文档**

- `docs/PROGRESS.md`：S3-2 勾选 + 新表格行（产出文件、验收数字：40 张阈值表、三档分布、测试计数）；
- `docs/DECISIONS.md`：
  - **D-058** 置信度公式与三档阈值：`ocr_base=0.35×锚点+0.65×值行`、`×agreement×rule_factor`、
    校验封顶 0.45、0.90/0.60 三档；理由（任务卡三档 + SCHEMA 乘法式延续 + S1-5 已有锚点强度/邻近折扣，
    只改组合方式不推翻证据结构）；权重初值的局限与 S6 用真实分布校准的计划；
  - **D-059** 队列口径：不新增 `invoices.status=needs_review`、`ocr_jobs.status=low_confidence`；
    队列 = `extract_status=need_confirm`；自动通过 ≠ `confirmed`；
- `docs/SCHEMA.md`/`docs/API.md`（Task 1/4 已改，若遗漏补齐）；
- `AGENTS.md` §10.8 追加锚点："置信度聚合：`services/confidence.py`（公式/三档/封顶 + 纯函数单测）＋
  `ocr/extractor.py` 证据成分（anchor_score/value_score/agreement）＋ `ocr_pipeline` 写回 | S3-2 ✅"；
- `docs/HANDOFF.md`：一句话现状改"S3-2 已通，下一步 S3-3"，§7 换成 S3-3 入口（crop 重识别 + 候选不自动覆盖）；
- `docs/spec/s3-2-confidence.md`、`docs/plan/s3-2-confidence.md` 状态改"已实现"。

- [ ] **Step 3: 提交**

```bash
git add docs AGENTS.md
git commit -m "docs(s3-2): 置信度口径、队列与实测结果收口"
```

---

## 自检（对照 spec）

| spec 条目 | 覆盖 Task |
|---|---|
| §1 产出 1–10 | Task 1–6 |
| §2 三处裁决（0.90/0.60、队列重定向、overall 均值） | Task 1、5、3 |
| §3 公式/三档/封顶/冲突降级/接口签名 | Task 1（实现）＋ Task 2（证据）＋ Task 3（应用） |
| §4 落库与 `extract_status` 语义 | Task 3 |
| §5 队列与前端 | Task 5（后端队列本来就有） |
| §6 七处冲突 | C1 Task1、C2/C3/C5 Task3+7、C4 Task1、C6 Task2、C7 Task4 |
| §7 测试 | Task 1/2/3/4 各自测试节 + Task 6 脚本 |
| §8 验收命令 | Task 7 |
| §9 收口 | Task 7 |

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 新阈值让队列过长/过短 | 改 `.env` 的 `CONFIDENCE_AUTO_CONFIRM/MANUAL_BELOW` 即可，代码不动；用 `confidence_eval.py` 看单调性 |
| 公式让分数整体偏低 | 调权重（仍须和为 1）或 `CONFIDENCE_VALIDATOR_CAP`；不要改抽取器的证据结构 |
| `review_level` 影响旧前端 | 字段可空；前端 `confidenceTag` 有数值回退分支 |
| 队列页重定向不符合预期 | 回退 `router/index.ts` 单行（后端 `extract_status` 筛选不受影响） |