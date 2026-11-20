# S3-2 字段级置信度聚合与低置信度确认队列 — 设计（spec）

> 任务卡：S3-2 ★关键 ｜ 契约：`docs/SCHEMA.md` §3.2/§3.3/§9.5、`docs/API.md` §2（列表 `extract_status` 筛选 / 详情字段）｜
> 上游：S3-1（`ocr_pipeline` 已落 `invoice_fields`、`ocr_lines`、`metrics`）｜
> 下游：S3-3（二次识别）、S3-5（详情页画框与候选交互）、S3-6（人工确认）
> 状态：**已实现（2026-11-20）**；实测：40 张 / 213 字段 → auto 101 / review 110 / manual 2，
> 阈值扫描单调不减，校验封顶与 auto≥0.90 不变量全过；单测 379 / 集成 173 全绿。

## 1. 目标与范围

把"要不要人工看"从抽取器的临时算术变成**可解释、可配置、可测**的规则，并形成 reviewer 能直接用的队列：

- 字段置信度 = 锚点行 score / 值行 score 的加权 **×** 取值一致性 **×** 规则强度；
- 三档判定：`auto`（≥0.90 免人工）/ `review`（0.60–0.90 待核对）/ `manual`（<0.60 必须人工）；
- 校验器判错的字段**封顶 0.45**（<0.5，任务卡要求）；
- 落库：`invoice_fields.confidence/need_review`、`invoices.overall_confidence`、`invoices.extract_status`；
- 队列：`GET /invoices?extract_status=need_confirm`（契约已有）+ 前端 `/review` 接上；
- 详情页置信度颜色/文案与新阈值对齐，并由后端下发 `review_level`（前端不硬编码阈值）。

**做**：

| # | 产出 | 说明 |
|---|---|---|
| 1 | `backend/app/services/confidence.py`（新） | 纯函数：`compute_field_confidence` / `apply_validator_cap` / `level_for` / `aggregate_invoice` |
| 2 | `backend/app/core/config.py` + `.env.example` | 阈值、权重、封顶值进 `Settings`（默认 0.90/0.60/0.45/0.35/0.65） |
| 3 | `backend/app/ocr/types.py` | `ExtractedField` 增 `anchor_score` / `value_score` / `agreement`（成分可追溯） |
| 4 | `backend/app/ocr/extractor.py` | 置信度改由 `confidence.py` 计算；校验失败由"折半"改为"封顶 0.45" |
| 5 | `backend/app/services/ocr_pipeline.py` | 写 `need_review`、`overall_confidence`、`extract_status`；`metrics` 记三档分布 |
| 6 | `backend/app/schemas/invoice.py` + `invoice_service._field_out` | 详情/字段输出增 `review_level`（`auto/review/manual`，服务端派生） |
| 7 | `web/src/router/index.ts` + `utils/landing.ts` | `/review` 重定向到 `/invoices?extract_status=need_confirm`；reviewer 登录落 `/review` |
| 8 | `web/src/views/InvoiceDetail.vue` | 置信度颜色/文案按 `review_level` 与 0.90/0.60 对齐 |
| 9 | `scripts/confidence_eval.py`（新） | 40 张离线跑：阈值扫描单调性 + 队列=低置信字段 + 校验失败必 <0.5 |
| 10 | `backend/tests/test_confidence.py`（新）+ 改 `test_extractor`/`test_ocr_pipeline`/`test_invoices` | 公式边界、封顶、聚合、落库、`review_level` |

**不做**：局部二次识别（S3-3）；`GET /invoices/{id}/fields`（S3-4）；人工确认动作与 `extract_status=confirmed`（S3-6）；
用于论文的"真实分布校准"（S6 用真实数据回填阈值，本卡只用合成集给初值与单调性证据）。

## 2. 已拍板的三处（用户 2026-11-20 确认）

| # | 问题 | 裁决 |
|---|---|---|
| Q1 | need_review 阈值：SCHEMA 写 0.85，任务卡写 0.90/0.60 | **按任务卡三段**：`≥0.90 auto`、`0.60–0.90 review`、`<0.60 manual`；SCHEMA §3.3 同步改，记 D-058 |
| Q2 | reviewer 队列前端做到哪一步 | **队列 = 重定向**：`/review` → `/invoices?extract_status=need_confirm`（复用筛选/分页/排序）；详情页阈值对齐 |
| Q3 | `overall_confidence` 口径 | **已抽到字段置信度的算术平均**（无字段 = 0）；只用于展示/排序，**不参与单字段判定**（任务卡坑②） |

## 3. 公式与判定（`services/confidence.py`）

```
ocr_base    = w_anchor × anchor_score + w_value × value_score        # 默认 0.35 / 0.65
raw         = ocr_base × agreement × rule_factor                      # 三者都在 [0,1]
if validator_failed:  raw = min(raw, validator_cap)                    # 默认 0.45（"直接 <0.5"）
score       = round(clamp(raw, 0, 1), 3)
level       = auto   if score >= auto_confirm(0.90)
              review elif score >= manual_below(0.60)
              manual otherwise
```

- `anchor_score`：命中锚点行的 OCR score；`value_score`：真正取值那一行的 OCR score
  （同行取值时两者相同；跨行邻近取值时 `value_score` 是值行、`agreement=0.85`）。
- `agreement`：同行直接取值 1.0；跨行邻近取值 0.85（沿用 S1-5 `_FUZZY_PENALTY`，不另设一套）。
- `rule_factor`：锚点优先级强度 1.0 / 0.92 / 0.84（沿用 S1-5 `_ANCHOR_STRENGTH`）。
- **缺成分的字段**（`paired:sum:*` / `derived:*` 这类推导值，没有锚点行与值行）：不走该乘法，
  保留抽取器给的保守值（0.55 / 0.60），再套校验封顶与三档判定；`anchor_score/value_score` 为 `None`。
- **冲突字段**（`candidates > 1`）：`level` 最高只到 `review`（强制人工看候选，不允许 auto）。
- 权重校验：两项都在 `[0,1]` 且和为 1（允许 0.01 误差），`auto_confirm > manual_below`，否则启动即 `ConfigError`。

`confidence.py` 的公开接口（纯函数，单测直接打）：

```python
ReviewLevel = Literal["auto", "review", "manual"]

@dataclass(frozen=True)
class FieldScore:                 # 输入：抽取器/流水线给出的证据
    field_name: str
    score: float                  # 已算好的原始分（含推导值 0.55/0.60）
    conflict: bool = False        # 同优先级多个候选
    validator_failed: bool = False

@dataclass(frozen=True)
class FieldConfidence:            # 输出：落库与前端用
    field_name: str
    score: float
    level: ReviewLevel

@dataclass(frozen=True)
class InvoiceConfidence:
    overall: float                # 字段置信度算术平均（无字段 0）
    needs_review: list[str]       # level != auto 的字段名（含冲突强制）
    levels: dict[str, str]        # 字段名 → auto/review/manual
    counts: dict[str, int]        # {"auto": n, "review": n, "manual": n}

def compute_field_confidence(anchor_score, value_score, agreement, rule_factor) -> float
def apply_validator_cap(score: float) -> float
def level_for(score: float) -> ReviewLevel
def resolve_field(item: FieldScore) -> FieldConfidence   # 封顶 + 冲突降级 + 三档
def aggregate_invoice(items: list[FieldConfidence]) -> InvoiceConfidence
```

`resolve_field` 的顺序固定：先 `apply_validator_cap`（失败才压到 0.45），再 `level_for`，
最后若 `item.conflict` 且 `level == "auto"` 则降为 `"review"`（冲突必须人看候选）。

## 4. 落库与状态（`ocr_pipeline`）

| 位置 | 写入 |
|---|---|
| `invoice_fields.confidence` | `confidence.level/score` 的结果（3 位小数） |
| `invoice_fields.need_review` | `level != auto` **或** 字段在 `validators.failed_fields()` 里 |
| `invoices.overall_confidence` | `aggregate_invoice.overall` |
| `invoices.extract_status` | 有任一 `need_review`、任一 `missing_required`、或任一冲突 → `need_confirm`；否则保持 `raw` |
| `ocr_jobs.metrics.counts` | 增 `needs_review`（字段数）、`review_levels={"auto","review","manual"}`、`overall_confidence` |

状态语义（写进 SCHEMA 说明）：`raw` = 机器抽完且无待核对项；`need_confirm` = 有字段/必填项要人看；
`confirmed` **只由人工确认动作设置**（S3-6），"自动通过"不等于"人工已确认"。

## 5. 队列与前端

- 后端**不新增队列接口**：`GET /invoices?extract_status=need_confirm` 是契约已有能力，天然就是 reviewer 队列；
  列表返回 `overall_confidence`，可按现有 `sort_by` 排序（不新增白名单字段）。
- `web/src/router/index.ts`：`/review` 由 `Placeholder` 改为 `redirect: '/invoices?extract_status=need_confirm'`；
  `utils/landing.ts`：reviewer 登录落 `/review`（submitter 仍 `/invoices`，admin 仍 `/templates`）。
- `InvoiceDetail.vue`：
  - 置信度标签改用后端 `review_level`：`manual → danger`、`review → warning`、`auto → success`；
  - 文案改"低于 90% 标黄、低于 60% 标红并必须人工核对"；
  - 字段表保留现有"待核对/冲突"标签，不再用 0.85 常量。
- `InvoiceList.vue`：不改（已有 `extract_status` 筛选与 URL 同步）。
- 验证：`npm run typecheck && npm run build`；reviewer 登录冒烟断言"待核对"页过滤生效（存在 `extract_status=need_confirm` 的票据时列表非空）。

## 6. 与任务卡/契约的冲突（报告，不静默选边）

| # | 冲突 | 处理 |
|---|---|---|
| C1 | SCHEMA §3.3 `need_review < 0.85` vs 任务卡 0.90/0.60 | Q1：按任务卡三段，SCHEMA 同步（D-058） |
| C2 | 任务卡"整单 needs_review 时 `ocr_jobs` 置 `low_confidence`" | **不采用**：`ocr_jobs.status` 契约 4 值（D-045 已拒）；"待确认"落 `invoices.extract_status`（D-059） |
| C3 | 任务卡 `GET /invoices?status=needs_review` | **不新增状态值**：`invoices.status` 是 5 值业务状态；用 `extract_status=need_confirm` |
| C4 | 公式形态：SCHEMA 乘法式 vs 任务卡"加权组合" | 混合式（§3）：锚点/值行加权 → 乘 agreement×rule_factor；SCHEMA §3.3 措辞同步 |
| C5 | 任务卡"≥0.9 自动确认" | **不置 `extract_status=confirmed`**：`confirmed` 是人工语义（S3-6），自动通过保持 `raw` |
| C6 | `ExtractedField` 契约新增三个成分字段 | 内部 OCR 契约扩展；S3-4 把它们写进导出 JSON Schema（PROGRESS 记一条） |
| C7 | 详情字段新增 `review_level` | API 契约新增派生字段（只读、不落库）；`API.md` §2 示例同步 |

## 7. 测试设计

**单元（默认 `pytest`）**
- `confidence.compute_field_confidence`：加权边界（0/1 两端）、`agreement=0.85` 单调下降、
  结果 3 位小数、`None` 成分走保守分支；
- `level_for`：0.90 恰好 auto（`>=`）、0.899→review、0.60 恰好 review、0.599→manual；
- `apply_validator_cap`：>0.45 被压到 0.45，<0.45 不反弹；
- `aggregate_invoice`：均值、空集 0、`needs_review` 集合、冲突强制 review、`levels/counts` 一致；
- 配置校验：权重和 ≠ 1、`auto <= manual`、越界值 → `ValidationError`；
- `extractor`：锚点命中时 `anchor_score/value_score/agreement` 正确；校验失败封顶（改掉原"折半"断言）；
  推导字段（`paired/derived`）`anchor_score is None` 且置信度仍为 0.55/0.60。

**集成（`-m integration`，临时库 + 假引擎）**
- 流水线落库：高置信字段 `need_review=false`、低置信/校验失败 `true`；`overall_confidence` = 均值；
  `extract_status`：全 auto → `raw`；有 review/manual → `need_confirm`；缺必填 → `need_confirm`；
  `metrics.counts.review_levels` 与库内一致；
- 冲突字段（同优先级两个候选）→ `need_review=true` 且 `review_level=review`（即使分数很高）；
- 详情接口字段带 `review_level`，与库内 confidence 对应；
- 队列：两个票据（一个 need_confirm、一个 raw）→ `GET /invoices?extract_status=need_confirm` 只返回前者。

**真实验收（`scripts/confidence_eval.py`，CPU）**
- 40 张合成票据离线跑（预处理/识别/抽取/置信度，不写库）；
- 输出阈值扫描 `{0.50,0.60,0.70,0.80,0.90,0.95}` 下的"字段需人工数 / 整单需核对数"，**必须单调不减**；
- 不变量：`validators` 判错的字段全部 `<0.5`；`level=auto` 的字段没有低于 0.90 的；
  "队列 = 低置信字段"按构造成立（断言 `needs_review == {f | score < 0.90 or conflict or failed}`）；
- 结果写 `backend/eval/results/confidence_eval_<ts>.json`，控制台打印表格 + 分布直方图（文本）。

## 8. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest -q                      # 全绿（新增 ≥15 条）
../.venv/Scripts/python.exe -m pytest -m integration -q
cd .. && .venv/Scripts/python.exe scripts/confidence_eval.py --samples 40
cd web && npm run typecheck && npm run build
# reviewer 登录冒烟（后端 + Vite 在跑）
node ../scripts/ui_smoke.mjs "http://127.0.0.1:5173/login" --clear --view 1440x900 \
  --fill 'input[autocomplete="username"]' <reviewer> --fill 'input[autocomplete="current-password"]' <口令> \
  --click 登录 --expect 待核对
```

## 9. 收口

- `PROGRESS` 勾 S3-2 + 实测（40 张阈值表、队列数与分布）；关闭遗留 #46 的"置信度阈值口径"部分；
- `DECISIONS`：D-058（公式/权重/阈值取定理由：任务卡三档 + SCHEMA 乘法式延续；S6 用真实分布校准）、
  D-059（队列 = `extract_status`，无 `needs_review` 状态；`ocr_jobs` 不加 `low_confidence`；auto ≠ confirmed）；
- `SCHEMA` §3.2/§3.3（overall 公式、need_review 阈值、extract_status 语义）；`API.md` §2（`review_level`、队列说明）；
- `AGENTS` §10.8 补"置信度聚合"锚点；`.env.example` 补 5 个配置；
- `HANDOFF` 下一步 S3-3；提交 `feat(s3-2): ...`（**push 由用户执行**）。