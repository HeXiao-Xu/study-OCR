# S3-4 识别产物契约与落库实现计划（plan）

> 对应 spec：[`docs/spec/s3-4-ocr-contract.md`](../spec/s3-4-ocr-contract.md)（已确认）｜
> 契约：`docs/API.md` §2 `GET /invoices/{id}/fields`、§11 数据对象 ｜ 状态：**已实现（2026-11-20）**，Task 1–6 全部执行并验证
> 执行方式：Task 1→6 顺序；每个 Task 跑完验证命令再进下一个。

**目标**：识别产物只有一份契约（`schemas/ocr.py`）→ 导出 JSON Schema → 前端 TS 对齐；
`invoice_fields.field_value/normalized_value` 按契约分开；补 `GET /fields`。**本卡无表变更、不加迁移。**

**架构**：模型放 `schemas/ocr.py`（唯一来源），`ocr/types.py` 只做 re-export；抽取器保留 `raw_value`；
流水线写两个值列；接口复用 `invoice_service._field_out`；JSON Schema 由脚本生成、测试防漂移。

**技术栈**：Python 3.11 / Pydantic v2 / SQLAlchemy 2.0 / pytest / Vue 3 + TS（无新增依赖）。

## Global Constraints

- 命令用 `.venv/Scripts/python.exe`；默认工作目录 `E:\BiSheXZY\study-OCR\backend`。
- **不加迁移**（head 保持 `0005`）；不改已提交迁移。
- 金额 `Decimal`；置信度 `NUMERIC(4,3)`；bbox 口径：`ocr_lines`=**校正图**四点、`invoice_fields`=**原图**外接框。
- 字段名以 `SCHEMA.md` 为准，禁止发明（`INVOICE_FIELD_NAMES` 是唯一白名单）。
- 测试不加载模型/不连 MinIO/Redis（集成用临时库 + 假实现）。
- 文档同步：`API.md` §2/§13、`SCHEMA.md` §3.3、`.env.example`（无新配置则不动）、`PROGRESS`、`DECISIONS`。
- 提交 Conventional Commits（`feat(s3-4): ...`）；**push 由用户执行**。

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/schemas/ocr.py`（新） | 唯一契约：`OcrLine/OcrResult/ExtractedField/FieldError/ExtractionResult` |
| `backend/app/ocr/types.py` | re-export（旧 import 不破） |
| `backend/app/ocr/extractor.py` | `_extract_matched`（raw+归一）；`_neighbour_value` 返回 raw |
| `backend/app/services/ocr_pipeline.py` | `field_value=raw_value or value`、`normalized_value=value` |
| `backend/app/schemas/invoice.py` + `services/invoice_service.py` + `api/v1/invoices.py` | `GET /fields` + `InvoiceFieldsData` |
| `backend/tests/{test_ocr_schema,test_extractor,test_ocr_pipeline,test_invoices}.py` | 契约/抽取/落库/接口测试 |
| `scripts/export_ocr_schema.py`（新） | 生成 `docs/schemas/ocr_result.schema.json`（`--check` 模式） |
| `docs/schemas/ocr_result.schema.json`（新，入库） | 唯一 JSON Schema 产物 |
| `web/src/types/ocr.ts`（新） | 前端类型（与契约逐字段对应） |
| `docs/API.md` §2/§13、`docs/SCHEMA.md` §3.3、`docs/DECISIONS.md`、`docs/HANDOFF.md`、`AGENTS.md` | 契约与收口 |

---

### Task 1: `schemas/ocr.py` 唯一契约 + `ocr/types.py` re-export

**Files:**
- Create: `backend/app/schemas/ocr.py`
- Modify: `backend/app/ocr/types.py`
- Test: `backend/tests/test_ocr_schema.py`（新）

**Interfaces:**
- Produces：`OcrLine / OcrResult / ExtractedField / FieldError / ExtractionResult`（`ExtractedField` 新增 `raw_value`）

- [ ] **Step 1: 写失败测试**（`tests/test_ocr_schema.py`，先只放模型契约基础用例）

```python
"""S3-4 识别产物契约：模型字段、Pydantic 校验、JSON Schema 与前端类型一致性。

这里不跑 OCR，只证明"契约本身是对的、且导出物与代码一致"。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.ocr import (
    ExtractedField,
    ExtractionResult,
    FieldError,
    OcrLine,
    OcrResult,
)


def _line(**overrides) -> OcrLine:
    payload = dict(
        text="价税合计（小写）￥1,234.56", score=0.95,
        box=[[10, 20], [300, 20], [300, 50], [10, 50]],
        line_no=3, y_center=35.0, x_left=10.0, height=30.0,
    )
    payload.update(overrides)
    return OcrLine(**payload)


def test_ocr_line_requires_four_points() -> None:
    with pytest.raises(ValidationError):
        _line(box=[[10, 20], [300, 20], [300, 50]])


def test_ocr_line_bbox_is_outer_rectangle() -> None:
    assert _line().bbox() == (10, 20, 300, 50)


def test_extracted_field_raw_value_is_optional() -> None:
    field = ExtractedField(
        name="total_amount", value="1234.56", raw_text="价税合计（小写）￥1,234.56",
        source_line_no=3, confidence=0.95, bbox=(10, 20, 300, 50),
    )
    assert field.raw_value is None

    with_raw = field.model_copy(update={"raw_value": "￥1,234.56"})
    assert with_raw.raw_value == "￥1,234.56"


def test_extracted_field_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValidationError):
        ExtractedField(name="x", value="1", raw_text="", confidence=1.5)


def test_field_error_has_severity() -> None:
    error = FieldError(field="total_amount", code="money_sum_mismatch", message="合计不等于金额+税额")
    assert error.severity == "error"


def test_ocr_result_carries_line_contract() -> None:
    result = OcrResult(lines=[_line()], image_size=(1000, 600))
    assert result.lines[0].text.endswith("1,234.56")
    assert result.engine == "paddleocr"


def test_extraction_result_defaults() -> None:
    result = ExtractionResult()
    assert result.fields == {} and result.missing_required == [] and result.ticket_type == "unknown"
```

- [ ] **Step 2: 确认失败**：`../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_schema.py`

预期 `ModuleNotFoundError: app.schemas.ocr`。

- [ ] **Step 3: 实现**

`app/schemas/ocr.py`（把 `ocr/types.py` 的内容搬过来，并新增 `FieldError` 与 `raw_value`）：

```python
"""OCR 识别产物契约（S3-4 起**唯一来源**）。

谁在用：`ocr/detector.py`（产出 OcrResult）、`ocr/extractor.py`（产出 ExtractionResult）、
`services/ocr_pipeline.py`（落库）、`scripts/export_ocr_schema.py`（导出 JSON Schema）、
前端 `web/src/types/ocr.ts` 与评测脚本。

坐标口径（写死，S3-3/D-060）：
- `OcrLine.box` = **四点** [[x,y]×4]，顺时针，**校正图**坐标系；
- `ExtractedField.bbox` = **[x1,y1,x2,y2]** 外接框，**原图**坐标系（前端画框只认这一种）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "ExtractedField",
    "ExtractionResult",
    "FieldError",
    "OcrLine",
    "OcrResult",
]


class OcrLine(BaseModel):
    """一行文本：内容 + 置信度 + 四点坐标（校正图坐标系）。"""

    text: str
    score: float = Field(ge=0.0, le=1.0)
    box: list[list[float]] = Field(description="四点坐标 [[x,y]×4]，顺时针，校正图坐标系")
    line_no: int = Field(ge=0, description="排序后的行号：从上到下、从左到右")
    y_center: float = Field(description="四点框的 y 中心，用于版面分块")
    x_left: float = Field(description="四点框的最小 x，用于行内排序")
    height: float = Field(description="四点框的高度，用于字号估计与分桶")

    @field_validator("box")
    @classmethod
    def _check_box(cls, value: list[list[float]]) -> list[list[float]]:
        if len(value) != 4 or any(len(point) != 2 for point in value):
            raise ValueError("box 必须是 4 个 [x, y] 点")
        return value

    def bbox(self) -> tuple[int, int, int, int]:
        """四点 → 外接矩形 [x1,y1,x2,y2]（字段层用；坐标仍是校正图）。"""
        xs = [point[0] for point in self.box]
        ys = [point[1] for point in self.box]
        return int(round(min(xs))), int(round(min(ys))), int(round(max(xs))), int(round(max(ys)))


class OcrResult(BaseModel):
    """一次识别的完整结果。"""

    lines: list[OcrLine] = Field(default_factory=list)
    image_size: tuple[int, int] = Field(description="(宽, 高)，校正图尺寸")
    engine: str = "paddleocr"
    engine_version: str = "2.7.3"
    elapsed_ms: float = 0.0
    det_params: dict = Field(default_factory=dict, description="实际使用的检测参数，写进 ocr_jobs.params_json")

    @property
    def is_empty(self) -> bool:
        return not self.lines

    @property
    def mean_score(self) -> float:
        return sum(line.score for line in self.lines) / len(self.lines) if self.lines else 0.0

    def find_by_keyword(self, keyword: str) -> list[OcrLine]:
        """按关键字筛行——S1-5 的"关键字锚定"用同一套筛选逻辑。"""
        return [line for line in self.lines if keyword in line.text]


class ExtractedField(BaseModel):
    """一个抽取出来的字段。保留来源行号、规则 id 与置信度成分，便于追溯与重算。"""

    name: str
    value: str = Field(description="归一值（金额 Decimal 字符串 / 日期 ISO / 纯数字码）")
    raw_value: str | None = Field(
        default=None, description="票面原文形态（如 ￥1,234.56）；推导值没有单一原文 → None"
    )
    raw_text: str = Field(description="命中的原始文本行，便于前端并排对照")
    source_line_no: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rule_id: str = ""
    rule_strength: float = 1.0
    # 置信度成分（S3-2）：锚点行 / 值行的 OCR score 与取值一致性
    anchor_score: float | None = Field(default=None, ge=0.0, le=1.0)
    value_score: float | None = Field(default=None, ge=0.0, le=1.0)
    agreement: float = Field(default=1.0, ge=0.0, le=1.0)
    bbox: tuple[int, int, int, int] | None = None
    candidates: list[str] = Field(default_factory=list, description="同分候选（有冲突时不自动替换）")


class FieldError(BaseModel):
    """校验器/确认动作给字段的错误（S3-6 的 422 明细也用它）。"""

    field: str
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


class ExtractionResult(BaseModel):
    """整张票的抽取结果。"""

    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ticket_type: str = "unknown"

    def value_of(self, name: str) -> str | None:
        item = self.fields.get(name)
        return item.value if item else None
```

`app/ocr/types.py` 整体替换为：

```python
"""OCR 契约（S3-4 起唯一来源是 `app/schemas/ocr.py`）。

保留本模块是为了不破坏 `ocr/` 内部与外部既有 import；**不要在这里再定义模型**。
"""
from app.schemas.ocr import (
    ExtractedField,
    ExtractionResult,
    FieldError,
    OcrLine,
    OcrResult,
)

__all__ = ["ExtractedField", "ExtractionResult", "FieldError", "OcrLine", "OcrResult"]
```

- [ ] **Step 4: 验证（含既有契约测试不回归）**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_schema.py tests/test_ocr_contract.py tests/test_extractor.py
../.venv/Scripts/python.exe -m pytest -q          # 全量不回归
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas/ocr.py backend/app/ocr/types.py backend/tests/test_ocr_schema.py
git commit -m "feat(s3-4): schemas/ocr.py 成为识别产物唯一契约（types 转 re-export）"
```

---

### Task 2: 抽取器保留票面原文（`raw_value`）

**Files:**
- Modify: `backend/app/ocr/extractor.py`
- Test: `backend/tests/test_extractor.py`

**Interfaces:**
- `_extract_matched(rule, text, anchor) -> tuple[str, str] | None`（raw, 归一值）
- `_neighbour_value(...) -> tuple[str, str, int] | None`（raw, value, line_no）
- 对外仍是 `extract_fields(...) -> ExtractionResult`，`ExtractedField.raw_value` 有值（推导值为 None）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_extractor.py`）

```python
def test_money_field_keeps_raw_value_and_normalized_value() -> None:
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    field = result.fields["total_amount"]
    assert field.value == "195.89"                 # 归一值
    assert field.raw_value == "￥195.89"           # 票面原文（带货币符号）
    assert field.raw_value != field.value


def test_date_field_raw_value_keeps_document_form() -> None:
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    field = result.fields["invoice_date"]
    assert field.value == "2025-11-04"
    assert field.raw_value is not None and "2025" in field.raw_value


def test_whole_line_name_field_keeps_raw_value() -> None:
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    field = result.fields["buyer_name"]
    assert field.value.startswith("武汉金穗")
    assert field.raw_value is not None


def test_derived_field_has_no_raw_value() -> None:
    from dataclasses import replace

    lines = [
        _line("电子发票（普通发票）", 50, 0),
        _line("发票号码：12345678", 90, 1),
        _line("开票日期：2025年11月04日", 120, 2),
        _line("金额  ¥184.80", 480, 3),
        _line("税额  ¥11.09", 520, 4),
    ]
    rules = [
        replace(rule, anchors=["不存在的锚点"]) if rule.name == "total_amount" else rule
        for rule in load_field_rules()
        if rule.name in {"amount", "tax_amount", "total_amount"}
    ]
    result = extract_fields(lines, (1200, 800), "vat_digital", rules=rules)
    derived = result.fields["total_amount"]
    assert derived.rule_id == "derived:total_amount"
    assert derived.raw_value is None


def test_neighbour_value_returns_raw_and_normalized() -> None:
    """跨行取值（below 方向）也要带原文，不能只回归一值。"""
    from app.ocr.extractor import _neighbour_value

    lines = [
        _line("开票日期", 300, 0),
        _line("2025年11月04日", 360, 1),
    ]
    rules = {rule.name: rule for rule in load_field_rules()}
    matched = _neighbour_value(rules["invoice_date"], lines[0], lines, 800)
    assert matched is not None
    raw, value, line_no = matched
    assert value == "2025-11-04"
    assert raw == "2025年11月04日"
    assert line_no == 1
```

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_extractor.py -k "raw_value or neighbour_value"
```

- [ ] **Step 3: 实现**

1. 用 `_extract_matched` 替换 `_extract_value` 的实现，并保留 `_extract_value` 作为包装（模块内其他地方可能还有调用）：

```python
def _extract_matched(rule: FieldRule, text: str, anchor: str) -> tuple[str, str] | None:
    """返回 (票面原文, 归一值)；归一不出来返回 None。"""
    if rule.whole_line_anchors and anchor in rule.whole_line_anchors:
        raw = _clean_text(_compact(text))
        normalized = _normalize(rule.value_type, raw)
        return (raw, normalized) if normalized else None

    tail = _clean_text(_tail_after_anchor(text, anchor))
    if not tail:
        return None
    if rule.regex:
        matches = [match.group(0) for match in re.finditer(rule.regex, tail)]
        if not matches:
            matches = [match.group(0) for match in re.finditer(rule.regex, text)]
        if not matches:
            return None
        if rule.value_index and len(matches) > rule.value_index:
            raw = matches[rule.value_index]
        else:
            raw = matches[0]
        normalized = _normalize(rule.value_type, raw)
        return (raw, normalized) if normalized else None
    normalized = _normalize(rule.value_type, tail)
    return (tail, normalized) if normalized else None


def _extract_value(rule: FieldRule, text: str, anchor: str) -> str | None:
    """兼容包装：只要归一值。"""
    matched = _extract_matched(rule, text, anchor)
    return matched[1] if matched is not None else None
```

2. `_neighbour_value` 返回三件套（内部收集也带 raw）：

```python
def _neighbour_value(rule: FieldRule, anchor_line: OcrLine, lines: list[OcrLine],
                     image_height: int) -> tuple[str, str, int] | None:
    # ……方向/距离筛选保持原样……
    collected: list[tuple[str, str, int]] = []
    for line in candidates:
        # ……zone/距离判断保持原样……
        if rule.regex:
            matches = re.findall(rule.regex, line.text)
            if not matches:
                continue
            if rule.value_index and len(matches) > rule.value_index:
                raw = matches[rule.value_index]
                normalized = _normalize(rule.value_type, raw)
                if normalized:
                    return raw, normalized, line.line_no
            for raw in matches:
                normalized = _normalize(rule.value_type, raw)
                if normalized:
                    collected.append((raw, normalized, line.line_no))
                if len(collected) > rule.value_index:
                    return collected[rule.value_index]
        else:
            raw = _clean_text(line.text)
            normalized = _normalize(rule.value_type, raw)
            if normalized:
                return raw, normalized, line.line_no
    return None
```

3. 锚点主循环改成 raw + value 双值，并透传到 `ExtractedField`：

```python
                matched = _extract_matched(rule, anchor_line.text, anchor)
                source_line = anchor_line
                strength = base_strength
                used_neighbour = False
                if matched is None:
                    neighbour = _neighbour_value(rule, anchor_line, ordered, image_height)
                    if neighbour is None:
                        continue
                    raw_value, value, source_line_no = neighbour
                    source_line = next(
                        (line for line in ordered if line.line_no == source_line_no), anchor_line
                    )
                    strength = base_strength * _FUZZY_PENALTY
                    used_neighbour = True
                else:
                    raw_value, value = matched
                agreement = _FUZZY_PENALTY if used_neighbour else 1.0
                hits.append(ExtractedField(
                    name=rule.name,
                    value=value,
                    raw_value=raw_value,
                    raw_text=source_line.text,
                    ...
                ))
```

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_extractor.py tests/test_validators.py
../.venv/Scripts/python.exe -m pytest -q
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/ocr/extractor.py backend/tests/test_extractor.py
git commit -m "feat(s3-4): 抽取器保留票面原文（ExtractedField.raw_value）"
```

---

### Task 3: 落库按契约分开两个值列

**Files:**
- Modify: `backend/app/services/ocr_pipeline.py`
- Test: `backend/tests/test_ocr_pipeline.py`

- [ ] **Step 1: 写失败测试**

单元（改 `field_rows` 用例，新增 raw_value 断言）：

```python
def test_field_rows_writes_raw_value_and_normalized_value() -> None:
    from app.services.confidence import FieldConfidence

    field = _field("total_amount", "1234.56", confidence=0.95).model_copy(
        update={"raw_value": "￥1,234.56"}
    )
    merged = {"total_amount": MergedField(field, 1)}

    rows = field_rows(
        merged, invoice_id=5, rule_ids={}, confidences={
            "total_amount": FieldConfidence("total_amount", 0.95, "auto")},
    )

    assert rows[0]["field_value"] == "￥1,234.56"     # 票面原文
    assert rows[0]["normalized_value"] == "1234.56"  # 归一值


def test_field_rows_falls_back_when_raw_value_missing() -> None:
    from app.services.confidence import FieldConfidence

    merged = {"buyer_name": MergedField(_field("buyer_name", "张三", confidence=0.95), 1)}
    rows = field_rows(
        merged, invoice_id=5, rule_ids={}, confidences={
            "buyer_name": FieldConfidence("buyer_name", 0.95, "auto")},
    )
    assert rows[0]["field_value"] == rows[0]["normalized_value"] == "张三"
```

集成（单条 INSERT + 原文入库）：

```python
@pytest.mark.integration
def test_run_pipeline_inserts_ocr_lines_in_one_statement(session_factory, owner, engine) -> None:
    from sqlalchemy import event

    statements: list[bool] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        if "insert into ocr_lines" in statement.lower():
            statements.append(bool(executemany))

    event.listen(engine, "before_cursor_execute", _before)
    try:
        invoice_id, job_id = _create_invoice(session_factory, owner.id)
        outcome, _ = _execute(
            session_factory, invoice_id, _deps(FakeEngine({0: _vat_digital_lines()}))
        )
    finally:
        event.remove(engine, "before_cursor_execute", _before)

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    assert statements == [True]      # 一次 executemany，不逐条 commit


@pytest.mark.integration
def test_run_pipeline_stores_raw_value_in_field_value(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    outcome, _ = _execute(
        session_factory, invoice_id, _deps(FakeEngine({0: _vat_digital_lines()}))
    )

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        row = db.scalar(select(InvoiceField).where(
            InvoiceField.invoice_id == invoice_id,
            InvoiceField.field_name == "total_amount",
        ))
        assert row.field_value == "￥1,234.56"          # 假引擎行里的原文
        assert row.normalized_value == "1234.56"
```

（`_vat_digital_lines` 的总计行是 `价税合计（小写）￥1,234.56`，规则 regex 抓到的 raw 就是 `￥1,234.56`。）

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_pipeline.py -k raw_value
../.venv/Scripts/python.exe -m pytest -m integration tests/test_ocr_pipeline.py -k "one_statement or raw_value"
```

- [ ] **Step 3: 实现**

`field_rows` 里两个值列：

```python
                "field_value": extracted.raw_value or extracted.value,
                "normalized_value": extracted.value,
```

（`line_rows` 的批量写法不动——`db.execute(insert(OcrLineRow), rows)` 已经是 executemany。）

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_pipeline.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_ocr_pipeline.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/ocr_pipeline.py backend/tests/test_ocr_pipeline.py
git commit -m "feat(s3-4): invoice_fields 按契约存票面原文与归一值，ocr_lines 单条批量 INSERT"
```

---

### Task 4: `GET /invoices/{id}/fields`

**Files:**
- Modify: `backend/app/schemas/invoice.py`、`backend/app/services/invoice_service.py`、`backend/app/api/v1/invoices.py`
- Modify: `docs/API.md` §2（fields 条：实现标记 + 冲突/字段说明）
- Test: `backend/tests/test_invoices.py`

**Interfaces:**
- `InvoiceFieldsData{invoice_id, overall_confidence, needs_confirm, conflicts, items}`
- `invoice_service.get_invoice_fields(db, user, invoice_id) -> InvoiceFieldsData`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_invoices.py` 详情区，用现有夹具）

```python
def test_fields_endpoint_returns_items_and_conflicts(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        db.add(InvoiceField(
            invoice_id=invoice_id, field_name="total_amount", field_value="￥1,234.56",
            normalized_value="1234.56", raw_text="价税合计（小写）￥1,234.56",
            source="ocr", confidence=Decimal("0.700"), need_review=True,
            conflict=True, conflict_value="1234.00", bbox_json=[1, 2, 3, 4],
        ))
        db.execute(sa_text("update invoices set overall_confidence = 0.75 where id = :id"),
                   {"id": invoice_id})
        db.commit()

    data = client.get(f"{BASE}/{invoice_id}/fields", headers=_auth(actors["alice"])).json()["data"]

    assert data["invoice_id"] == invoice_id
    assert data["overall_confidence"] == "0.750"
    assert data["needs_confirm"] == ["total_amount"]
    assert data["conflicts"] == [{
        "field_name": "total_amount", "reason": "candidate_conflict",
        "expected": "￥1,234.56", "actual": "1234.00",
    }]
    item = data["items"][0]
    assert item["field_name"] == "total_amount"
    assert item["review_level"] == "review"
    assert item["normalized_value"] == "1234.56"


def test_fields_endpoint_empty_and_permissions(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)

    empty = client.get(f"{BASE}/{invoice_id}/fields", headers=_auth(actors["alice"])).json()["data"]
    assert empty["items"] == [] and empty["needs_confirm"] == [] and empty["conflicts"] == []

    assert client.get(f"{BASE}/{invoice_id}/fields", headers=_auth(actors["bob"])).status_code == 404
    assert client.get(f"{BASE}/{invoice_id}/fields", headers=_auth(actors["ada"])).status_code == 404
    assert client.get(f"{BASE}/{invoice_id}/fields", headers=_auth(actors["rita"])).status_code == 200
```

（`overall_confidence` 的输出形态与 `InvoiceOut` 一致：两位小数字符串；若现有 schema 里是 `Decimal` 直出，
按 `_money` 风格统一——用 `_money(invoice.overall_confidence)`。）

- [ ] **Step 2: 确认失败**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_invoices.py -k fields_endpoint
```

- [ ] **Step 3: 实现**

`schemas/invoice.py`：

```python
class InvoiceFieldsData(BaseModel):
    invoice_id: int
    overall_confidence: Decimal | None = None   # 与 InvoiceOut 一致：JSON 里是三位小数字符串
    needs_confirm: list[str] = Field(default_factory=list)
    conflicts: list[ConflictOut] = Field(default_factory=list)
    items: list[InvoiceFieldOut] = Field(default_factory=list)
```

`invoice_service.py`：

```python
def get_invoice_fields(db: Session, user: User, invoice_id: int) -> InvoiceFieldsData:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    rows = db.scalars(
        select(InvoiceField)
        .where(InvoiceField.invoice_id == invoice.id)
        .order_by(InvoiceField.field_name.asc())
    ).all()
    conflicts = [
        ConflictOut(
            field_name=row.field_name,
            # 只回"抽取阶段留下的候选冲突"；money_sum 等校验原因由 S3-6 的 confirm 计算
            reason="candidate_conflict",
            expected=row.field_value,
            actual=row.conflict_value,
        )
        for row in rows
        if row.conflict
    ]
    return InvoiceFieldsData(
        invoice_id=invoice.id,
        overall_confidence=invoice.overall_confidence,
        needs_confirm=[row.field_name for row in rows if row.need_review],
        conflicts=conflicts,
        items=[_field_out(row) for row in rows],
    )
```

`api/v1/invoices.py`（放在详情之后）：

```python
@router.get(
    "/{invoice_id}/fields",
    response_model=Envelope[InvoiceFieldsData],
    summary="字段级识别结果（原文/归一值/置信度/冲突）",
)
def get_invoice_fields(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(invoice_service.get_invoice_fields(db, user, invoice_id))
```

`docs/API.md` §2 fields 条：

- 实现改 `S3-4 ✅`；
- 备注补：`items[].review_level` 服务端派生；`conflicts[].reason` 当前只有 `candidate_conflict`
  （校验类原因在确认接口 S3-6 给出）；`field_value` 是票面原文、`normalized_value` 是归一值。

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_invoices.py
../.venv/Scripts/python.exe -m pytest -q tests/test_api_docs.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas/invoice.py backend/app/services/invoice_service.py \
  backend/app/api/v1/invoices.py docs/API.md backend/tests/test_invoices.py
git commit -m "feat(s3-4): 实现 GET /invoices/{id}/fields（原文/归一值/冲突）"
```

---

### Task 5: JSON Schema 导出 + 前端 TS 类型 + 防漂移

**Files:**
- Create: `scripts/export_ocr_schema.py`、`docs/schemas/ocr_result.schema.json`（脚本生成后入库）、
  `web/src/types/ocr.ts`
- Modify: `backend/tests/test_ocr_schema.py`（追加 3 条防漂移用例）、`docs/API.md`（§13）、
  `docs/SCHEMA.md` §3.3
- 依赖：`docs/schemas/` 目录新建

- [ ] **Step 1: 写脚本**

```python
"""导出识别产物 JSON Schema（S3-4）。

    .venv/Scripts/python.exe scripts/export_ocr_schema.py           # 写 docs/schemas/ocr_result.schema.json
    .venv/Scripts/python.exe scripts/export_ocr_schema.py --check   # 只比对，不一致退出码 1（测试用）

为什么提交生成的 JSON：前端/评测脚本要对照字段名，没有文件就只能靠人读 Python 源码。
漂移由 `tests/test_ocr_schema.py` 把关（重算后与文件深度比较）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from pydantic.json_schema import models_json_schema  # noqa: E402

from app.schemas.ocr import (  # noqa: E402
    ExtractedField,
    ExtractionResult,
    FieldError,
    OcrLine,
    OcrResult,
)

SCHEMA_PATH = ROOT / "docs" / "schemas" / "ocr_result.schema.json"
MODELS = {
    "OcrLine": OcrLine,
    "OcrResult": OcrResult,
    "ExtractedField": ExtractedField,
    "FieldError": FieldError,
    "ExtractionResult": ExtractionResult,
}


def build_schema() -> dict:
    definitions, _ = models_json_schema(
        [(model, "validation") for model in MODELS.values()]
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://study-ocr.local/schemas/ocr_result.schema.json",
        "title": "OCR 识别产物契约（S3-4）",
        "description": "OcrLine.box 为四点（校正图坐标）；ExtractedField.bbox 为 [x1,y1,x2,y2]（原图坐标）",
        "$defs": definitions.get("$defs", {}),
    }


def render(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 OCR 产物 JSON Schema")
    parser.add_argument("--check", action="store_true", help="只比对不写盘")
    args = parser.parse_args()

    rendered = render(build_schema())
    if args.check:
        if not SCHEMA_PATH.exists():
            print(f"缺少 {SCHEMA_PATH}", file=sys.stderr)
            return 1
        if SCHEMA_PATH.read_text(encoding="utf-8") != rendered:
            print("JSON Schema 与模型不一致：请重新运行 scripts/export_ocr_schema.py", file=sys.stderr)
            return 1
        print("schema OK")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(rendered, encoding="utf-8")
    print(f"已写入 {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

生成并检查：

```bash
cd /e/BiSheXZY/study-OCR && .venv/Scripts/python.exe scripts/export_ocr_schema.py
.venv/Scripts/python.exe scripts/export_ocr_schema.py --check
```

- [ ] **Step 2: 前端类型**（`web/src/types/ocr.ts`，字段名与后端**逐字一致**）

```ts
/** 识别产物契约（S3-4）：与 backend/app/schemas/ocr.py 逐字段对应，见 docs/schemas/ocr_result.schema.json。 */

export interface OcrLine {
  text: string
  score: number
  /** 四点坐标 [[x,y]×4]，顺时针，校正图坐标系 */
  box: number[][]
  line_no: number
  y_center: number
  x_left: number
  height: number
}

export interface OcrResult {
  lines: OcrLine[]
  image_size: [number, number]
  engine: string
  engine_version: string
  elapsed_ms: number
  det_params: Record<string, unknown>
}

export interface ExtractedField {
  name: string
  /** 归一值（金额两位小数 / 日期 ISO / 纯数字码） */
  value: string
  /** 票面原文形态；推导值为 null */
  raw_value: string | null
  raw_text: string
  source_line_no: number | null
  confidence: number
  rule_id: string
  rule_strength: number
  anchor_score: number | null
  value_score: number | null
  agreement: number
  /** [x1,y1,x2,y2] 外接框，原图坐标系 */
  bbox: [number, number, number, number] | null
  candidates: string[]
}

export interface FieldError {
  field: string
  code: string
  message: string
  severity: 'error' | 'warning'
}

export interface ExtractionResult {
  fields: Record<string, ExtractedField>
  missing_required: string[]
  conflicts: Record<string, unknown>[]
  warnings: string[]
  ticket_type: string
}
```

- [ ] **Step 3: 追加防漂移测试**（`tests/test_ocr_schema.py`）

```python
import json
import re
from pathlib import Path

from app.models.enums import TicketType
from app.schemas.ocr import ExtractedField, ExtractionResult, FieldError, OcrLine, OcrResult
from app.services.invoice_service import INVOICE_FIELD_NAMES

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "schemas" / "ocr_result.schema.json"
TS_PATH = ROOT / "web" / "src" / "types" / "ocr.ts"

_MODELS = {
    "OcrLine": OcrLine,
    "OcrResult": OcrResult,
    "ExtractedField": ExtractedField,
    "FieldError": FieldError,
    "ExtractionResult": ExtractionResult,
}


def test_committed_json_schema_matches_models() -> None:
    from export_ocr_schema import build_schema   # conftest 已把仓库根 scripts/ 加入 sys.path

    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == build_schema()


def _ts_interface_fields(name: str) -> set[str]:
    body = re.search(rf"export interface {name} \{{(.*?)\n\}}", TS_PATH.read_text(encoding="utf-8"), re.S)
    assert body is not None, f"types/ocr.ts 缺少 interface {name}"
    return set(re.findall(r"^\s{2}([a-z_][a-z0-9_]*)\??:", body.group(1), re.M))


def test_frontend_types_match_contract() -> None:
    for name, model in _MODELS.items():
        assert _ts_interface_fields(name) == set(model.model_fields), f"types/ocr.ts 的 {name} 与后端不一致"


def test_template_field_names_are_in_whitelist() -> None:
    from app.services.template_seed import build_seed_templates

    templates = build_seed_templates()
    names = {name for template in templates for name in template.field_specs_json}
    assert names, "seed 模板没有字段规格"
    assert names <= set(INVOICE_FIELD_NAMES), f"模板里出现白名单外字段：{sorted(names - set(INVOICE_FIELD_NAMES))}"
```

注意：`tests/conftest.py` 已把仓库根 `scripts/` 加入 `sys.path`，因此直接
`from export_ocr_schema import build_schema` 即可（不是 `scripts.export_ocr_schema`）。

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_schema.py
cd .. && .venv/Scripts/python.exe scripts/export_ocr_schema.py --check
```

- [ ] **Step 5: API.md §13 + SCHEMA §3.3**

`docs/API.md` 末尾新增：

```markdown
## 13. 识别产物契约（S3-4）
- 唯一来源：`backend/app/schemas/ocr.py`；导出的 JSON Schema 见 `docs/schemas/ocr_result.schema.json`
  （生成/校验：`python scripts/export_ocr_schema.py [--check]`）；前端类型见 `web/src/types/ocr.ts`。
- 模型：`OcrLine`（一行文本）、`OcrResult`（一次识别）、`ExtractedField`（一个字段，含归一值与票面原文）、
  `FieldError`（校验/确认错误）、`ExtractionResult`（整票抽取结果）。
- **坐标口径写死**：`OcrLine.box` = 四点 `[[x,y]×4]`（顺时针、**校正图**坐标）；
  `ExtractedField.bbox` = `[x1,y1,x2,y2]` 外接框（**原图**坐标，前端画框只认这一种）。
- **字段名白名单**：凡出现 `field_name`/`ExtractedField.name` 的地方，取值只能是
  `invoice_service.INVOICE_FIELD_NAMES`（`tests/test_ocr_schema.py` 防漂移）。
- 字段级接口 `GET /invoices/{id}/fields` 返回的 `items` 即 `invoice_fields` 行（含 `review_level` 派生字段），
  `field_value` 是票面原文、`normalized_value` 是归一值。
```

`docs/SCHEMA.md` §3.3 说明段补：

```markdown
> 写入来源（S3-4）：`field_value` 取 `ExtractedField.raw_value`（票面原文，如 `￥1,234.56`），
> `normalized_value` 取 `ExtractedField.value`（归一值，如 `1234.56`）；推导值（`derived:*`/`paired:*`）
> 没有单一原文，`field_value` 回退为归一值。
```

- [ ] **Step 6: 提交**

```bash
git add scripts/export_ocr_schema.py docs/schemas/ocr_result.schema.json web/src/types/ocr.ts \
  backend/tests/test_ocr_schema.py docs/API.md docs/SCHEMA.md
git commit -m "feat(s3-4): 导出 OCR 产物 JSON Schema，前端类型与契约对齐"
```

---

### Task 6: 真实接口冒烟 + 全量回归 + 文档收口

- [ ] **Step 1: 全量验证**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check            # 应仍为 0005，无新迁移
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd .. && .venv/Scripts/python.exe scripts/export_ocr_schema.py --check
cd web && npm run typecheck && npm run build
```

- [ ] **Step 2: 真实接口冒烟**（起 uvicorn + worker；临时账号）

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe ../scripts/create_user.py --username s34sub --role submitter \
  --real-name S34验收 --password 'S34sub!2026'
cd /e/BiSheXZY/study-OCR && .venv/Scripts/python.exe - <<'PYEOF'
import time, httpx
from pathlib import Path
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=120)
tok = c.post("/api/v1/auth/login",
             json={"username": "s34sub", "password": "S34sub!2026"}).json()["data"]["access_token"]
H = {"Authorization": f"Bearer {tok}"}
with Path("samples/synth_v1/vat_digital_landscape_001.jpg").open("rb") as handle:
    up = c.post("/api/v1/invoices/upload", headers=H,
                files={"file": ("a.jpg", handle, "image/jpeg")})
iid = up.json()["data"]["invoice_id"]
for _ in range(60):
    status = c.get(f"/api/v1/invoices/{iid}/ocr-status", headers=H).json()["data"]
    if status["ocr_status"] in ("succeeded", "failed"):
        break
    time.sleep(2)
data = c.get(f"/api/v1/invoices/{iid}/fields", headers=H).json()["data"]
print("overall:", data["overall_confidence"])
print("needs_confirm:", data["needs_confirm"])
print("conflicts:", data["conflicts"])
for item in data["items"]:
    print(item["field_name"], "|", item["field_value"], "|", item["normalized_value"], "|",
          item["confidence"], item["review_level"])
print("invoice_id", iid)
PYEOF
```

判定：`field_value` 出现 `¥`/`￥` 或千分位（证明是票面原文）、`normalized_value` 是纯值；
`review_level` 三档有值；`needs_confirm` 与 `need_review` 一致。冒烟后清理票据/对象/账号。

- [ ] **Step 3: 文档收口**

- `PROGRESS`：勾 S3-4 + 表格行（产出、Schema 文件、fields 冒烟、测试计数）；关闭遗留 #52；
  新增遗留：旧 `field_value=归一值` 的历史数据不迁移（本地无；生产需重跑识别）；
- `DECISIONS`：**D-062**（契约唯一来源 `schemas/ocr.py` + re-export；`raw_value` 与两个值列口径；
  本卡不加迁移；`GET /fields` 的 conflicts 只回候选冲突；JSON Schema 文件 + 三项防漂移测试）；
- `AGENTS` §10.8 补"识别产物契约"锚点（`schemas/ocr.py` + 导出脚本 + 防漂移测试 + `raw_value` 口径）；
- `HANDOFF`：现状改"S3-4 已通，下一步 S3-5"，§7 换 S3-5 入口（左图右表 + canvas 画框 + bbox 换算 +
  只渲染视口附近框 + 低置信黄底/校验失败红底）；§4 指标表补 Schema 产物；
- `docs/spec|plan/s3-4-*` 状态改"已实现"。

- [ ] **Step 4: 提交**

```bash
git add docs AGENTS.md
git commit -m "docs(s3-4): 识别产物契约、raw_value 口径与实测收口"
```

---

## 自检（对照 spec）

| spec 条目 | 覆盖 Task |
|---|---|
| §1 产出 1–10 | Task 1–5（10 为文档，Task 5/6） |
| §2 C1 无迁移 | 全卡；Task 6 明确 `alembic check` |
| §2 C2 raw_value | Task 2、Task 3 |
| §2 C3 `GET /fields` | Task 4 |
| §2 C4 防漂移 | Task 5 |
| §2 C5 坐标口径 | Task 1 模型注释 + Task 5 API §13 |
| §2 C6 落库契约 | Task 3 |
| §3.1–3.6 设计 | Task 1–5 |
| §4 测试 | 各 Task 测试节 |
| §5 验收命令 | Task 6 |
| §6 收口 | Task 6 |

## 回滚与降级

| 情况 | 处理 |
|---|---|
| `raw_value` 改变 `field_value` 形态导致前端显示异常 | 前端 `displayValue` 用 `field_value ?? normalized_value`，显示原文是期望行为；若编辑回写失败由 `_normalize_field` 兜底（现有逻辑） |
| JSON Schema 与模型漂移 | `--check` 与单测会失败；重新跑导出脚本并提交 |
| 前端 TS 字段改名 | `test_frontend_types_match_contract` 会失败；先改后端契约再改 TS |
| 历史数据 `field_value` 是归一值 | 不自动迁移（本地无此类数据）；生产需重跑识别，记 PROGRESS 遗留 |
| `GET /fields` 与详情字段重复 | 详情接口保留（页面已用）；`/fields` 供 S3-5/S3-6 与评测脚本，两者共用 `_field_out` |