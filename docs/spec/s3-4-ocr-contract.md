# S3-4 识别结果 JSON Schema 与落库契约 — 设计（spec）

> 任务卡：S3-4 ｜ 契约：`docs/API.md` §2（`GET /invoices/{id}/fields`）、§11（数据对象）、
> `SCHEMA.md` §3.3/§3.4/§9.8 ｜ 上游：S1-3（`OcrLine/OcrResult`）、S1-5（`ExtractedField`）、S3-1（落库）、
> S3-3（bbox 坐标口径）｜ 下游：S3-5（画框）、S3-6（人工确认）、S6（评测脚本共用契约）
> 状态：**已实现（2026-11-20）**；实测：JSON Schema 5 模型入库且 `--check` 通过、`GET /fields` 返回
> 票面原文 `￥1,796.37`/归一值 `1796.37`、三档 `review_level` 与冲突；单元 415 / 集成 198 全绿，head 仍 0005（无迁移）。

## 1. 目标与范围

识别产物只有**一份**契约：Pydantic 模型 → 导出的 JSON Schema → 前端 TS 类型 → 评测脚本，全部对齐；
同时把 `invoice_fields` 的两个值列按契约分开存（票面原文 vs 归一值），并补上漏掉的 `GET /fields`。

**做**：

| # | 产出 | 说明 |
|---|---|---|
| 1 | `backend/app/schemas/ocr.py`（新，**唯一来源**） | `OcrLine / OcrResult / ExtractedField / FieldError / ExtractionResult`；`ExtractedField.raw_value` 新增 |
| 2 | `backend/app/ocr/types.py` | 改为从 `schemas/ocr.py` re-export（保留 `__all__`，旧 import 不破） |
| 3 | `backend/app/ocr/extractor.py` | `_extract_value` → `_extract_matched`（返回 raw + 归一值）；`_neighbour_value` 同步；`ExtractedField.raw_value` 落原文 |
| 4 | `backend/app/services/ocr_pipeline.py` | `field_value = raw_value or value`、`normalized_value = value`；补"单条 INSERT"测试 |
| 5 | `backend/app/services/invoice_service.py` + `schemas/invoice.py` + `api/v1/invoices.py` | 实现契约 `GET /invoices/{id}/fields`（items/review_level/needs_confirm/conflicts/overall） |
| 6 | `scripts/export_ocr_schema.py`（新） | 生成 `docs/schemas/ocr_result.schema.json`（入库） |
| 7 | `backend/tests/test_ocr_schema.py`（新） | Schema 防漂移 + 前端 TS 字段比对 + 字段名白名单 |
| 8 | `web/src/types/ocr.ts`（新） | 与后端契约逐字段对应（`snake_case`，与 `types/invoice.ts` 一致） |
| 9 | `docs/API.md` §13（新）+ §2 fields 条 | "识别产物契约"节：模型字段表、bbox 坐标口径、JSON Schema 路径、生成命令 |
| 10 | `docs/SCHEMA.md` §3.3 | `field_value/normalized_value` 的写入来源写明（`raw_value`/归一值；推导值回退） |

**不做**：新表/新列（本卡**不需要迁移**，理由见 §2 C1）；`field_reread` 审计行（S4-7）；画框与确认交互（S3-5/S3-6）；
`conflicts` 的实时校验（留给确认动作，见 §2 C3）。

## 2. 与任务卡/现状的差异（先报告）

| # | 任务卡/契约 | 现状 | 处理 |
|---|---|---|---|
| C1 | "新增字段走新 alembic revision" | `ocr_lines`/`invoice_fields` 的列 S2-1 已建、S3-1 已落库；本卡没有表变更 | **不加迁移**（head 保持 `0005`）。造一个空迁移属于假动作，不做 |
| C2 | `invoice_fields.field_value` = 票面原文 | 抽取器只返回归一值，S3-1 起 `field_value` 存的是归一值（遗留 #52） | 本卡补 `ExtractedField.raw_value` 并纠正写入；**旧数据不迁移**（本地无字段行；生产需重跑，记遗留） |
| C3 | `GET /invoices/{id}/fields`（契约标 S3-2/S3-4） | S3-2 未实现 | 本卡实现；`conflicts` **只回已落库的候选冲突**（`reason=candidate_conflict`），实时校验留给 S3-6 confirm |
| C4 | 前端 TS 与后端契约共用 | 手写类型会漂移 | 生成 JSON Schema + **自动比对测试**（后端模型 ↔ TS 接口字段名） |
| C5 | bbox 坐标口径 | S3-3 已定：`ocr_lines`=校正图、`invoice_fields`=原图 | 写进 JSON Schema 描述与 API §13，测试钉住 `ocr_lines` 仍存四点 |
| C6 | 任务卡把 `ocr_lines/invoice_fields` 写入放在 S3-4 | S3-1 已实现批量 `insert` | 本卡只补"契约字段 ↔ 落库列"对照与单条 INSERT 断言，不重写落库 |

## 2.1 已拍板（用户 2026-11-20 确认）

| # | 问题 | 裁决 |
|---|---|---|
| Q1 | Pydantic 契约放哪 | **定义放 `schemas/ocr.py`**，`ocr/types.py` 改为 re-export（单一来源；旧 import 不破） |
| Q2 | `GET /fields` 的 `conflicts` 口径 | **只回已落库的候选冲突**（`reason=candidate_conflict`），不实时重跑校验器（留给 S3-6） |
| Q3 | 导出的 JSON Schema 怎么落 | **生成 `docs/schemas/ocr_result.schema.json` 入库 + 防漂移测试**，`API.md` 新增 §13 引用 |

## 3. 设计

### 3.1 `schemas/ocr.py`（唯一来源）

```python
class OcrLine(BaseModel):
    text: str
    score: float = Field(ge=0, le=1)
    box: list[list[float]] = Field(description="四点坐标 [[x,y]×4]，顺时针，校正图坐标系")
    line_no: int = Field(ge=0)
    y_center: float; x_left: float; height: float
    def bbox(self) -> tuple[int, int, int, int]      # 四点 → 外接框（字段层用）

class OcrResult(BaseModel):
    lines: list[OcrLine]; image_size: tuple[int, int]
    engine: str = "paddleocr"; engine_version: str = "2.7.3"
    elapsed_ms: float = 0.0; det_params: dict = {}

class ExtractedField(BaseModel):
    name: str
    value: str                  # 归一值（金额两位小数 / 日期 ISO / 纯数字码）
    raw_value: str | None = None  # 票面原文形态（如 "¥1,234.56"）；推导值没有原文 → None
    raw_text: str; source_line_no: int | None
    confidence: float = Field(ge=0, le=1)
    rule_id: str = ""; rule_strength: float = 1.0
    anchor_score: float | None; value_score: float | None; agreement: float = 1.0
    bbox: tuple[int, int, int, int] | None
    candidates: list[str] = []

class FieldError(BaseModel):
    field: str; code: str; message: str; severity: Literal["error", "warning"] = "error"

class ExtractionResult(BaseModel):
    fields: dict[str, ExtractedField]
    missing_required: list[str] = []
    conflicts: list[dict] = []
    warnings: list[str] = []
    ticket_type: str = "unknown"
```

`ocr/types.py` 变成：

```python
"""OCR 契约（S3-4 起唯一来源是 schemas/ocr.py；本模块只做兼容导出）。"""
from app.schemas.ocr import ExtractedField, ExtractionResult, FieldError, OcrLine, OcrResult
__all__ = ["ExtractedField", "ExtractionResult", "FieldError", "OcrLine", "OcrResult"]
```

### 3.2 抽取器：保留票面原文

把内部函数改成"返回 raw + 归一值"，对外的 `ExtractionResult` 语义不变：

```python
def _extract_matched(rule, text, anchor) -> tuple[str, str] | None:
    """返回 (票面原文, 归一值)；归一不出来返回 None。"""

def _extract_value(rule, text, anchor) -> str | None:      # 保留：内部兼容包装
    matched = _extract_matched(rule, text, anchor)
    return matched[1] if matched else None
```

- 锚点同行命中：`raw_value, value = matched`；
- 邻近行命中：`_neighbour_value` 返回 `(raw_value, value, line_no)`；
- `whole_line_anchors` 路径：raw = 去首尾标点的整行，value = 归一值；
- `paired:sum:*` / `derived:*`：`raw_value=None`（没有单一原文），`raw_text` 保留"（按合计关系…）"说明。

### 3.3 落库（`ocr_pipeline.field_rows`）

```python
"field_value": extracted.raw_value or extracted.value,   # 契约：票面原文形态
"normalized_value": extracted.value,                      # 契约：归一值
```

批量写入保持现状（`db.execute(insert(OcrLineRow), rows)` 是 executemany 一次往返），
新增测试用 SQLAlchemy 事件统计"INSERT INTO ocr_lines"只出现 **1** 次。

### 3.4 `GET /api/v1/invoices/{id}/fields`（契约 §2）

- 权限：本人或 `reviewer`；`admin` → 404（`repository.get_visible_or_404`）；
- 出参（`API.md` §2 原样）：

```python
class InvoiceFieldsData(BaseModel):
    invoice_id: int
    overall_confidence: Decimal | None
    needs_confirm: list[str]              # need_review=true 的字段名（排序）
    conflicts: list[ConflictOut]          # 已存候选冲突：reason="candidate_conflict"
    items: list[InvoiceFieldOut]          # 复用详情字段结构（含 review_level）
```

- `conflicts[].expected = field_value`（当前采纳值）、`actual = conflict_value`（另一候选）；
- 无字段 → `items=[]`、`needs_confirm=[]`、`conflicts=[]`、`overall_confidence` 取票据列（可为空），不 404；
- 未实现校验原因（`money_sum_mismatch` 等）不在此接口重复计算——确认动作（S3-6）负责，避免两处规则漂移。

### 3.5 JSON Schema 导出与防漂移

`scripts/export_ocr_schema.py`：

- 用 `pydantic.json_schema.models_json_schema` 把 5 个模型放进 `$defs`，输出
  `docs/schemas/ocr_result.schema.json`（`$schema` = 2020-12、`ensure_ascii=False`、键排序、2 空格缩进）；
- 脚本 `--check` 模式只比对不写盘（CI/测试用）。

`tests/test_ocr_schema.py`：

1. `test_committed_schema_matches_models`：内存生成 vs 已入库 JSON 深度相等；
2. `test_frontend_types_match_contract`：解析 `web/src/types/ocr.ts` 的 `export interface X { field: ... }`，
   与对应 Pydantic 模型字段名**完全一致**（缺/多都失败）；
3. `test_field_names_match_schema_whitelist`：`template_seed.build_seed_templates()` 的字段规格键
   ⊆ `invoice_service.INVOICE_FIELD_NAMES`（防规则里出现 SCHEMA 没有的字段名）；
4. `test_ocr_line_box_is_four_points`：`ocr_lines` 的写入契约要求四点 `[[x,y]×4]`（`line_rows` 单元断言）。

### 3.6 前端类型

`web/src/types/ocr.ts`：`OcrLine/OcrResult/ExtractedField/FieldError/ExtractionResult`，字段名与后端**逐字一致**
（snake_case，与 `types/invoice.ts` 同风格）；不新增依赖、不引第三方生成器。

## 4. 测试设计

**单元（默认 `pytest`）**
- `schemas/ocr.py`：JSON Schema 必填项、`score/confidence` 边界、`box` 四点校验、`raw_value` 可空；
- `extractor`：金额/日期/编码/邻近行/whole_line 的 `raw_value` 与 `value` 不同且都正确；推导值 `raw_value is None`；
  旧断言（`value` 与置信度）不改语义；
- `field_rows`：有 `raw_value` 时 `field_value≈原文`、`normalized_value=归一值`；无 raw 时回退归一值；
- `ocr_line` 四点/bbox 契约不变。

**集成（临时库 + 假引擎）**
- 落库对照：`ocr_lines` 行数 = 假引擎行数、`box_json` 四点、`score` 三位小数；
  "INSERT INTO ocr_lines" 事件计数 = 1（批量，不逐条 commit）；
- `invoice_fields.field_value` 是 `¥`/千分位形态的原文（用假引擎返回带 `¥1,234.56` 的行）；
- `GET /fields`：字段/`review_level`/`needs_confirm`/`conflicts`（构造 `conflict=True` 行）/整体置信度；
  本人 200、他人 404、reviewer 200、admin 404、无字段空结构。

**真实验收**
- `scripts/export_ocr_schema.py` 生成并 `--check` 通过；`python -m json.tool docs/schemas/ocr_result.schema.json` 合法；
- 起后端 + worker，上传一张 → `GET /invoices/{id}/fields` 出参含原文值与 `review_level`；
- 前端 `npm run typecheck && npm run build`。

## 5. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check          # head 仍 0005，本卡无迁移
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd .. && .venv/Scripts/python.exe scripts/export_ocr_schema.py --check
cd web && npm run typecheck && npm run build
```

## 6. 收口

- `PROGRESS` 勾 S3-4 + 实测（Schema 文件、fields 接口冒烟、测试计数）；关闭遗留 #52；
  新增遗留：旧 `field_value=归一值` 的历史数据不迁移（本地无；生产需重跑）；
- `DECISIONS`：**D-062**（契约唯一来源 `schemas/ocr.py` + re-export；`raw_value` 与两个值列口径；
  本卡不加迁移的理由；`GET /fields` 的 conflicts 只回候选冲突；JSON Schema 文件 + 三项防漂移测试）；
- `API.md` §13 + §2 fields；`SCHEMA.md` §3.3 写入来源；`AGENTS` §10.8 补"识别产物契约"锚点；
  `HANDOFF` 下一步 S3-5；
- 提交 `feat(s3-4): ...`（push 由用户）。