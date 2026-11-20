# S3-3 局部二次识别（crop 放大重识别）— 设计（spec）

> 任务卡：S3-3 ｜ 契约：`docs/API.md` §2 `POST /invoices/{id}/reocr`（**已定义，异步 202**）、
> `SCHEMA.md` §3.3/§3.4/§9.8 ｜ 上游：S1-3（`recognizer.recognize_line`）、S3-1（`ocr_lines` 四点坐标）、
> S3-2（`confidence.resolve_field` 判档）｜ 下游：S3-5（画框/候选交互）、S3-6（人工确认）
> 状态：**已实现（2026-11-20）**；实测：40 张 / 112 个低置信字段 → 109 个提升（97.3%）、平均 Δ +0.180；
> HTTP 冒烟 amount 0.743→0.952、tax 0.690→0.946；单元 396 / 集成 194 全绿，迁移 head=0005。

## 1. 目标与范围

人工触发一次"只重识别这几个字段"，用裁剪+放大把低置信字段救回来；**不重跑整图**、**不覆盖人工值**。

**做**：

| # | 产出 | 说明 |
|---|---|---|
| 1 | `backend/app/ocr/refine.py`（新） | 裁剪原语：四点框→外扩→clamp→放大→`recognize_line`；越界/空裁剪抛 `ValidationFailedError` |
| 2 | `backend/app/services/reocr_service.py`（新） | `submit_reocr`（校验+建作业+入队）、`run_reocr`（worker 侧执行与写回） |
| 3 | `backend/app/api/v1/invoices.py` + `schemas/invoice.py` | `POST /{id}/reocr`（202）+ `ReocrRequest/ReocrData` |
| 4 | `backend/app/tasks/{worker,ocr_task,queue}.py` | `reocr_task` + `enqueue_reocr`（复用 `job_service` 状态机/幂等/重试） |
| 5 | `backend/app/core/config.py` + `.env.example` | `REREAD_PAD_MIN_PX/REREAD_PAD_RATIO/REREAD_SCALE/REREAD_BUDGET_SECONDS` |
| 6 | `backend/alembic/versions/0005_audit_action_field_reread.py` | `audit_logs.action` 放开 `field_reread`（**只放开 CHECK，不写数据**） |
| 7 | `backend/app/services/ocr_pipeline.py` | **修 S3-1 的坐标偏差**：`invoice_fields.bbox_json` 写原图坐标 |
| 8 | `backend/tests/{test_refine,test_reocr,test_ocr_pipeline}.py` | 裁剪边界、接口校验、写回、坐标映射 |
| 9 | `scripts/reread_eval.py`（新） | 40 张离线：挑低置信字段重识别，输出 old/new 文本与分数、提升率 |
| 10 | `web/src/views/InvoiceDetail.vue` | 低置信字段行加"重识别"按钮，触发 `reocr` 后刷新（**不加候选选择 UI**，那是 S3-5/S3-6） |

**不做**：任务卡的两个同步接口 `POST /invoices/{id}/fields/{field}/reread`、`POST /invoices/{id}/reread-region`
（契约里没有，且 `reocr` 已覆盖"按字段重识别"；"整块区域"没有业务入口，S3-5 画框再说）；
自动改字段**值**（用户裁决：只改 `raw_text/confidence`，值由人工在 S3-6 选择）；审计**行**（哈希链 S4-7）。

## 2. 四处置信（用户 2026-11-20 确认）

| # | 冲突 | 裁决 |
|---|---|---|
| Q1 | API.md 是异步 `reocr`，任务卡是两个同步接口 | **按 API.md**：`POST /invoices/{id}/reocr` 202 + `job_id`，前端轮询详情；任务卡两接口不落地 |
| Q2 | "候选列表由人选择" vs 契约"取更高分自动写" | **只改 `raw_text/confidence/source=ocr_retry`，`field_value` 不动**；old/new 候选写进 `ocr_jobs.metrics.fields[]` 供追溯；改值归 S3-6 |
| Q3 | `audit_logs.action` 缺 `field_reread` | **加枚举 + 迁移 0005**（只放开 CHECK）；审计**行**留 S4-7（哈希链未实现，现在写行等于伪造 hash） |
| Q4 | S3-1 把字段 bbox 写成校正图坐标（SCHEMA 要求原图） | **本卡修正**：用 `PreprocessResult.to_original` 映射回原图（PDF 恒等） |

## 3. 接口契约（写入 `API.md` §2 并标记已实现）

### POST /api/v1/invoices/{id}/reocr

- 权限：**本人**（契约原文；`reviewer` 能否触发留 S3-6 与"确认权限"一起定，本卡按契约只放本人）｜
  类型：异步（202）｜ 幂等：否（有进行中的重识别时 409）｜ 实现：S3-3
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| fields | array | 是 | 1–6 个、去重；必须是该票据已抽到的字段 |
| version | int | 否 | 传则做乐观锁校验（409） |

- 出参：**202**，`data` = `{invoice_id, job_id, fields}`（`job_id` 是 `ocr_jobs.id`，stage=`recognize`）
- 错误：`404/40400` 票据不可见；`403/40301` 非本人；`409/40900` 票据状态不允许（`status != draft` 或
  `extract_status not in {raw, need_confirm}`）、版本冲突、已有进行中的重识别；
  `422/42200` 字段数越界/字段不存在/字段是 `manual` 或 `confirmed`/已是 `ocr_retry`/无 bbox 或无来源行；
- 备注：worker 按 `invoice_fields.source_page_no + source_line_no` 回查 `ocr_lines.box_json`（**校正图四点坐标**）
  → 外扩 `max(8px, 10%×行高)` → clamp → `INTER_CUBIC` 放大 2×（长边封顶 1024）→ `recognize_line`；
  **仅当新分 > 旧 confidence** 时更新 `raw_text/confidence/source=ocr_retry`（`field_value` 不变）；
  然后重算 `invoices.overall_confidence` 与 `extract_status`；单张总增量 ≤1.5s，超预算的字段记 `skipped` 后正常结束；
  逐字段 old/new 明细写 `ocr_jobs.metrics.fields[]`；识别为空（非文本区域）的字段记 `skipped:no_text`，不使整单失败。

## 4. 裁剪原语（`ocr/refine.py`）

```python
# 以下常量只是**函数默认值**，运行时由 Settings（REREAD_*）覆盖，便于调参与消融
PAD_MIN_PX = 8            # 契约备注：bbox 外扩 8px
PAD_RATIO = 0.10          # 任务卡：四周各扩 10%（两者取 max，窄行也能扩够）
SCALE = 2.0               # 契约：放大 2 倍（任务卡 2–3 倍，取契约值）
MAX_SIDE = 1024           # 放大后长边上限，防单字段推理拖垮 1.5s 预算
MIN_CROP_PX = 8           # clamp 后小于该尺寸视为空裁剪

@dataclass(frozen=True)
class RereadCandidate:
    text: str
    score: float
    bbox: tuple[int, int, int, int]      # 实际裁剪框（校正图坐标）
    scale: float

def expand_bbox(bbox, *, width, height, pad_min_px=8, pad_ratio=0.10) -> tuple[int,int,int,int]
    # 以 bbox 高/宽较小者为基准取 max(8px, ratio×边长) 四周外扩，再 clamp 到 [0,w]×[0,h]

def crop_region(image, bbox, *, pad_min_px=8, pad_ratio=0.10) -> tuple[np.ndarray, tuple[int,int,int,int]]
    # 越界（与图无交集）、退化（宽或高 < MIN_CROP_PX）、入参非法 → ValidationFailedError（422）

def upscale(crop, *, factor=2.0, max_side=1024) -> np.ndarray      # INTER_CUBIC + 长边封顶

def reread_box(image, box4_or_bbox, *, engine=None, factor=2.0) -> RereadCandidate
    # box4 = ocr_lines 四点（校正图坐标）；内部先取外接框再 crop→upscale→recognize_line

def bbox_to_original(bbox, to_original: np.ndarray | None) -> tuple[int,int,int,int]
def bbox_from_original(bbox, to_original: np.ndarray | None) -> tuple[int,int,int,int]
    # to_original 为 None（PDF）→ 恒等；否则按 3×3 变换四角再取外接框（S3-5 画框与区域裁剪共用）
```

坐标口径（写进 `API.md`/`SCHEMA` 备注）：
- `ocr_lines.box_json` = **校正图**四点坐标（识别发生在校正图上）；
- `invoice_fields.bbox_json` = **原图**外接框（前端画框只认这个）；
- 字段级重识别直接用 `ocr_lines` 的四点（校正图），**不做逆映射**。

## 5. 服务与任务

`services/reocr_service.py`

```python
def submit_reocr(db, user, invoice_id, payload, *, queue) -> ReocrData
```
1. `get_visible_or_404`；`user.id != invoice.uploader_id` → `RoleForbiddenError`（契约"本人"）；
2. 状态：`status != draft` 或 `extract_status not in {raw, need_confirm}` → `ConflictError`；
   有 `version` 且不匹配 → `ConflictError`；
3. 字段校验（全部在一次 `SELECT` 里取回来判定）：存在、非 `manual`、非 `confirmed`、`source != ocr_retry`、
   `bbox_json` 非空、`source_line_no/source_page_no` 非空；任一不满足 → `ValidationFailedError`（422）；
4. 已有 `pending/running` 的 reocr 作业（`stage='recognize' AND params_json->>'kind'='reocr'`）→ 409；
5. `job_service.create_job(db, invoice_id, stage='recognize')` + `params_json={"kind":"reocr","fields":[...]}`，
   commit 后 `queue.enqueue_reocr(job_id)`；入队失败 → `mark_enqueue_failed` + 503（与上传一致）。

```python
def run_reocr(db, job) -> None        # 作为 job_service.execute_job_attempt 的 handler
```
1. 读 `params_json.fields`；读 invoice/file；票据已不是 draft → `ValidationFailedError`（作业 failed，不重试）；
2. 下载一次（`storage.get_bytes`，`is_cipher=true` 同 S3-1：待 S4）；`prepare_pages(path, max_pages=max(source_page_no))`；
3. 预算 `deadline = monotonic() + settings.reread_budget_seconds`；逐字段：
   - `ocr_lines` 回查 `(invoice_id, page_no, line_no)`；缺失 → `skipped:missing_line`；
   - `refine.reread_box(page.image, box_json)`；空文本/score<0.3 → `skipped:no_text`；超预算 → `skipped:budget`；
   - `applied = candidate.score > float(row.confidence or 0)`；applied 时：
     `row.raw_text = candidate.text`（**不动 field_value/normalized_value**）、
     `row.confidence = Decimal(round(score,3))`、`row.source = 'ocr_retry'`、
     `row.need_review = confidence.level_for(score) != 'auto'`、`row.updated_at = now`；
4. 重算整单：`aggregate_invoice([...])`（按库内所有字段的 confidence 判档）→ 写 `invoices.overall_confidence`、
   `extract_status`（有 need_review → need_confirm，否则 raw；重识别阶段看不到 `missing_required`，不重复判定缺必填）；
5. `job.metrics = {"kind":"reocr","budget_ms":…,"elapsed_ms":…,"fields":[{field_name,old_value,old_confidence,
   new_text,new_score,applied,reason,bbox}],"applied":n,"skipped":n}`；不 commit（交 `job_service` 终态提交）。

`tasks/ocr_task.py::reocr_task(ctx, job_id)` 与 `ocr_task` 同构（`RetryLater` → ARQ `Retry`）；
`tasks/queue.py` 的 `OcrTaskQueue` 协议加 `enqueue_reocr(job_id)`；`WorkerSettings.functions` 注册。

## 6. 坐标修正（S3-1 偏差）

`ocr_pipeline` 写 `invoice_fields.bbox_json` 前调用 `refine.bbox_to_original(bbox, page.preprocess.to_original)`：
- 照片：`PreprocessResult.to_original` 存在 → 映射四角取外接框；
- PDF：无预处理 → `None` → 恒等。
`ocr_lines.box_json` 保持校正图坐标不动。测试用构造的 3×3 缩放矩阵钉住映射结果。

## 7. 审计枚举（迁移 0005）

- `models/enums.py::AuditAction` 增 `FIELD_REREAD = "field_reread"`；
- 迁移 0005：drop `ck_audit_logs_action` → 按 `enum_check` 重新生成（沿用 0002 的写法）；
- **不写审计行**：`audit_logs.prev_hash/entry_hash` 非空且 64 位 hex 校验，哈希链 S4-7 才有实现；
  现在写行只能伪造 hash（AGENTS §7 禁止假数据）。PROGRESS 记遗留"field_reread 审计行待 S4-7"。
- SCHEMA §9.x 审计动作清单同步补 `field_reread`。

## 8. 前端（最小）

`InvoiceDetail.vue` 字段表每行加"重识别"按钮（仅 `draft`/`need_confirm` 且有 `source_line_no` 时可见）：
点一次 → `POST /invoices/{id}/reocr {fields:[name], version}` → 提示"已提交重识别"→ 轮询详情（3s×5 次），
刷新后若 `source=ocr_retry` 出现"已重识别"标签；**不弹候选选择**（S3-5/S3-6）。
`api/invoices.ts` 加 `reocrField(invoiceId, fieldName, version)`；`types/invoice.ts` 加 `ReocrData`。
失败（403/409/422）用 `ApiError.message` 原样提示，不吞。

## 9. 测试设计

**单元（默认 `pytest`）**
- `refine.expand_bbox`：完全在内、部分越界→clamp、完全越界→`ValidationFailedError`、退化框→错误、
  pad 取 `max(8px, 10%)` 的两个分支；
- `refine.upscale`：2× 尺寸、长边封顶、单通道不报错；
- `refine.crop_region`：空裁剪/宽高 <8px/非法 bbox（负数、x2<x1）→ 422；
- `refine.bbox_to_original` / `bbox_from_original`：恒等、缩放 2×、平移；往返一致（误差 ≤1px）；
- `schemas.ReocrRequest`：0 个/7 个/重复字段 → 422。

**集成（临时库 + 假 engine/storage/prepare + 假队列）**
- 提交：本人 202 + 假队列收到 `job_id`；他人 404；reviewer 403；`status=confirmed` 409；`extract_status=confirmed` 409；
  版本冲突 409；未知字段 422；manual/confirmed 字段 422；已 `ocr_retry` 422；无 `bbox/source_line_no` 422；
  已有进行中 reocr 409；队列不可用 → 503 + job failed；
- worker：假引擎返回更高分文本 → `raw_text/confidence/source=ocr_retry` 更新且 **`field_value` 不变**、
  `need_review` 按新分判档、整单 `overall_confidence/extract_status` 重算；
  假引擎返回更低分 → 字段不动；空文本 → `skipped:no_text`；预算 0s → 全部 `skipped:budget`；
- 队列/worker 注册：`WorkerSettings` 含 `reocr_task`；`enqueue_reocr` 被调用；
- 流水线 bbox：伪造 `to_original=2×` 的 `PreprocessResult` → `invoice_fields.bbox_json` 为放大后坐标。

**真实验收**
- `scripts/reread_eval.py --samples 40`：离线跑全链路，挑 `confidence < 0.9` 的字段（优先 `total_amount`），
  裁剪重识别，输出：可重识别字段数、提升数（new_score > old + 0.05）、平均提升、文本变化样例、
  "越界/空裁剪"守卫是否触发；结果写 `backend/eval/results/reread_eval_<ts>.json`。
- HTTP 冒烟（起后端 + worker）：上传一张 → 取低置信字段 → `POST /reocr` 202 → 轮询详情 →
  字段 `source=ocr_retry`、`confidence` 变化；越界字段（手工造 bbox 为空的行）→ 422。

## 10. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic upgrade head && ../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd .. && .venv/Scripts/python.exe scripts/reread_eval.py --samples 40
cd web && npm run typecheck && npm run build
```

**验收指标**：低置信字段重识别后至少一部分字段分数上升（如实记录提升率，不达预期就写明）；
越界/空裁剪 422 而不是 500；`field_value` 永不因重识别改变；人工值（manual/confirmed）不可被触发。

## 11. 与任务卡/契约的冲突（报告）

| # | 冲突 | 处理 |
|---|---|---|
| C1 | 任务卡两个同步 reread 接口 vs API.md `reocr`（异步） | 按契约（Q1）；任务卡两接口不实现，PROGRESS 记原因 |
| C2 | 任务卡"候选列表由人选择" vs 契约"取更高分自动写" | Q2：只改 raw_text/confidence；候选进 metrics；值归 S3-6 |
| C3 | 任务卡外扩 10% / 放大 2–3× vs 契约 8px / 2× | 混合：`max(8px, 10%)` + 2×（契约值），参数可配 |
| C4 | 任务卡要求写 `audit_logs(action=field_reread)` | Q3：加枚举 + 0005；审计行留 S4-7（现在写=伪造 hash） |
| C5 | `invoice_fields.bbox_json` 实际是校正图坐标（S3-1 偏差） | Q4：本卡修正为原图坐标 |
| C6 | 契约 `reocr` 权限只写"本人"，reviewer 无法在核对台触发 | 按契约实现；reviewer 权限与 S3-6 一起定，PROGRESS 记遗留 |
| C7 | 任务卡"非文本区域返回 422" | 异步 `reocr` 无法同步返回 422：识别为空记 `skipped:no_text`，不使整单失败；同步 422 只覆盖**入参/定位非法**（字段无 bbox、越界裁剪由 `refine` 抛 422 但只在 worker 里） |

## 12. 收口

- `PROGRESS` 勾 S3-3 + 实测（reread_eval 提升率、接口冒烟、测试计数）；新增遗留：
  `field_reread` 审计行待 S4-7、reviewer 触发权限待 S3-6、region 重识别未做；
- `DECISIONS`：D-060（接口按契约异步 reocr；只改 raw_text/confidence，值不自动改；坐标修正）；
  D-061（外扩 8px/10% 取 max + 2× 放大 + 1.5s 预算；审计只加枚举不写行）；
- `SCHEMA` §9 审计动作 + `API.md` §2 `reocr` 实现标记与细节；`.env.example` 4 项；
- `AGENTS` §10.8 补"局部重识别"锚点；`HANDOFF` 下一步 S3-4；
- 提交 `feat(s3-3): ...`（push 由用户）。