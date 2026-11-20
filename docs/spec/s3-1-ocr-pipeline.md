# S3-1 OCR Worker 串起全链路 — 设计（spec）

> 任务卡：S3-1 ★关键 ｜ 契约：`docs/API.md` §2（上传 202 / `GET /invoices/{id}/ocr-status`）＋
> `docs/SCHEMA.md` §3.1–3.5（`invoice_files` / `invoices` / `invoice_fields` / `ocr_lines` / `ocr_jobs`）｜
> 上游：S2-6 上传落库 + S2-7 ARQ 作业框架（`ocr_task._run_pipeline` 占位）｜
> 下游：S3-2 置信度聚合、S3-4 识别产物契约、S3-5 详情页画框
> 状态：**待用户确认**（确认后写 plan，再写码）

## 1. 目标与范围

把 S1 的脚本链路（`scripts/ocr_baseline.py` 里已验证的那条）搬进 ARQ worker：
上传 → 入队 → **下载 → 渲染/预处理 → 检测识别 → 票种 → 抽取 → 校验 → 落库**，
全程不手工跑脚本；失败可见、可定位；`ocr-status` 能轮询到阶段进度。

**做**：

| # | 产出 | 说明 |
|---|---|---|
| 1 | `backend/app/services/ocr_pipeline.py`（新） | 流水线唯一入口 `run_pipeline(db, job)`；纯函数式编排 + 计时 + 落库 |
| 2 | `backend/app/tasks/ocr_task.py` | `_run_pipeline` 改为委托 `ocr_pipeline.run_pipeline`；任务级拒绝"重入" |
| 3 | `backend/app/ocr/intake.py` | 新增 `prepare_pages(source, max_pages=3)`：PDF 渲染前 3 页、照片单页；`prepare_input` 行为不变 |
| 4 | `backend/app/services/storage.py` | 新增 `get_bytes(key, max_bytes)`（流式读、限长、对象缺失/不可用分类） |
| 5 | `backend/app/services/job_service.py` | 写 `started_at/finished_at`；终态写入前校验 `(status, retry_count)` 防僵尸线程覆写（`superseded`） |
| 6 | `backend/app/services/ocr_status_service.py`（新）+ `api/v1/invoices.py` | 实现 `GET /invoices/{id}/ocr-status`（API.md 契约） |
| 7 | `backend/app/schemas/invoice.py` | `OcrStatusData` / `OcrStageOut`（Pydantic v2）；详情 `latest_job` 结构与字段**不改** |
| 9 | `backend/alembic/versions/0004_ocr_jobs_metrics_times.py` | `ocr_jobs` 加 `metrics`、`started_at`、`finished_at` |
| 10 | `backend/app/ocr/extractor.py` | 新公开 `rules_from_specs(specs)`（DB `field_specs_json` → `FieldRule`，与 S2-8 试跑同源） |
| 11 | `backend/tests/{test_ocr_pipeline,test_ocr_status}.py` | 单测（假 engine/storage）+ 集成（临时库） |
| 12 | `scripts/ocr_acceptance.py`（新） | 真实 20 张验收：成功率 / P95 / 临时目录残留 / `--cleanup` |
| 13 | `web/src/api/invoices.ts` + `views/Upload.vue` + `types/invoice.ts` | `waitForOcr` 换 `ocr-status`，上传页显示阶段进度 |

**不做（明确划出边界）**：
- `GET /invoices/{id}/fields`（S3-4）——详情接口已返回 fields，S3-1 不新增此路由；
- 字段级置信度聚合公式、`overall_confidence`、`extract_status=need_confirm`、reviewer 确认队列（S3-2）；
- 局部二次识别（S3-3）、识别产物 Pydantic 契约与 JSON Schema 导出（S3-4）；
- `invoice_fields.field_value` 的"票面原文形态"（抽取器目前不暴露原始子串，见 §4 缺口 6）；
- MinIO 密文解密（S4）——只留接缝；
- 运维"重跑失败作业"入口（PROGRESS 遗留 #35，S3-8 前后再补；本卡只保证同 job_id 不重复执行）。

## 2. 已拍板的三处契约缺口（用户 2026-11-20 确认）

| # | 缺口 | 裁决 |
|---|---|---|
| G1 | 任务卡要求把 `timings` 写 `ocr_jobs.metrics(JSONB)`，但 SCHEMA/模型没有 `metrics` 列 | **加列 + 迁移 0004**：`ocr_jobs.metrics JSONB`、`started_at TIMESTAMPTZ`、`finished_at TIMESTAMPTZ`。`params_json` 仍只放"阶段参数"，不与耗时混用 |
| G2 | SCHEMA §3.5 字面是"每个阶段一条作业记录"，但表里没有 `run_id`，多行无法归组到同一次运行，`ocr-status.stages` 也拼不出来 | **一次运行一行**：沿用"1 个 ARQ 作业 = 1 行"。`stage` 列记录**当前/最后阶段**，逐阶段状态与耗时写 `metrics.stages`。SCHEMA §3.5 措辞同步改（D-055） |
| G3 | 上传页轮询仍在用详情接口 | **换**：`waitForOcr` 改调 `ocr-status`，上传页显示 `progress` 与阶段；PROGRESS 遗留 #42 关闭 |

`ocr-status` 的 `started_at/finished_at` 与 `stages` 由此**有真数据**（不是用 `created_at + elapsed_ms` 反推的近似值）。

## 3. 设计

### 3.1 数据流（`services/ocr_pipeline.run_pipeline(db, job)`）

```
ctx: db(Session), job(OcrJob: id/invoice_id/retry_count/attempt)
 0. 取 job/invoice/file；`job.status != running` → 抛 `AbortAttempt`（`job_service` 回滚业务写入、不动状态，返回 `superseded`）
 1. invoice = db.get(Invoice, job.invoice_id)；file = db.get(InvoiceFile, invoice.file_id)
    ├ invoice 不存在/已 void → 永久失败（AbortPipeline）
    └ 记录 metrics.attempt
 2. invoice.ocr_status: pending|running → running（**唯一允许中途提交的业务列**，见 §3.6）
 3. bytes = storage.get_bytes(file.storage_key, max_bytes=max_upload_mb)
    └ file.is_cipher=true → 抛 ValidationFailedError("加密影像解密尚未接入（S4）")（永久失败，重试无意义）← S4 接缝
 4. with tempfile.TemporaryDirectory() as tmp:   # try/finally 由 with 保证
        src = tmp/"source.bin"；写盘 → intake.prepare_pages(src, max_pages=3)
    # PDF：渲染前 min(3, page_count) 页，不做预处理（D-035）；照片：preprocess 单页
 5. 逐页：engine.recognize(page.image)（worker 级单例）
        layout.assign_regions(...) 取 block_id；lines 收集，带 page_no
    └ 页 1（或首个有行的页）classify_ticket → ticket_type（unknown 时顺延）
 6. 规则来源：registry.active_templates(db)[ticket_type].field_specs_json
        → extractor.rules_from_specs(...)；DB 无该票种 → 回退 load_field_rules()（metrics 记 warning）
 7. 逐页 extract_fields(...)（内含 run_validators）→ 多页合并：**首页优先，后续页只补空字段**，
        同名字段不同值 → metrics.warnings 记 `page_conflict:<name>`，不覆盖
 8. 落库（同一事务，见 §3.5）：
        ocr_lines（先删后插，page_no/line_no/四点 box_json/score/block_id）
        invoice_fields（upsert 语义；(invoice_id, field_name) 唯一键；跳过 manual/confirmed 行）
        invoices 冗余列 + invoice_type + template_id + quality_flag + ocr_status=succeeded
        ocr_jobs.stage=extract、metrics=终版（**不 commit**，交 job_service 与 succeeded 一起提交）
```

`needs_retake` **不阻断**（`intake` 的既定语义）：记 `quality_flag=low` + `retake_hints` 进 metrics，
前端在详情页提示；PDF 无预处理 → `quality_flag` 保持 `NULL`（不编造"质量合格"）。

### 3.2 `metrics` JSONB 结构（版本化，`version: 1`）

```json
{
  "version": 1,
  "attempt": 1,
  "pages_total": 2, "pages_ocr": 2,
  "stages": [
    {"stage": "preprocess", "status": "succeeded", "elapsed_ms": 88},
    {"stage": "detect", "status": "succeeded", "elapsed_ms": null,
     "note": "DBNet 与 SVTR 在同一次 PaddleOCR 调用内完成，无法单独计时"},
    {"stage": "recognize", "status": "succeeded", "elapsed_ms": 1420},
    {"stage": "extract", "status": "succeeded", "elapsed_ms": 12}
  ],
  "timings_ms": {"download": 35, "intake": 88, "ocr": 1420, "layout": 3, "extract": 12,
                 "persist": 23, "total": 1602},
  "counts": {"lines": 118, "fields": 12, "missing_required": ["check_code"], "conflicts": 1},
  "engine": {"name": "paddleocr", "version": "2.7.3", "det_params": {"det_db_unclip_ratio": 1.6}},
  "rules_source": "db|yaml", "template_id": 3,
  "quality": {"flag": "ok", "needs_retake": false, "retake_hints": [], "reasons": []},
  "warnings": ["missing_required:check_code", "page_conflict:total_amount"],
  "duplicate_invoice_no": false
}
```

约束：`warnings` 去重且**最多 20 条**；**禁止**写入票面明文、税号、金额原值、对象名（`field_rules` 错误码类字符串安全）。

### 3.3 `ocr_jobs` 迁移（0004，只加列，不改已应用迁移）

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `metrics` | `JSONB` | 是 | 逐阶段耗时/状态、引擎参数、警告（不含明文） |
| `started_at` | `TIMESTAMPTZ` | 是 | 本次尝试开始（`job_service` 在 `running` 时写） |
| `finished_at` | `TIMESTAMPTZ` | 是 | 进入终态时写（`succeeded/failed`；回到 `pending` 重试时不写） |

`alembic check` 必须零差异；SCHEMA §3.5 先行同步（表 + 说明 + 提交语义段落）。

> 为什么推翻 S2-7 spec §2"不新增 `started_at/finished_at`"：那时只为作业状态机服务，
> `created_at + elapsed_ms` 够用。S3-1 起 `ocr-status` 契约（API.md §2）**要求**这两个字段，
> 用近似值会造成"开始时间早于上传完成"这类无法解释的数据。显式加列 + 记 D-056。

### 3.4 `GET /api/v1/invoices/{id}/ocr-status`

- 权限：本人或 `reviewer`；`admin` → 404（走 `repository.get_visible_or_404`，与其他票据接口一致）；
- 出参（`API.md` §2 逐字对齐）：
  `{invoice_id, ocr_status, progress, stages: [{stage,status,elapsed_ms,engine,engine_version,retry_count,error}], started_at, finished_at}`；
- `stages` 来自 `metrics.stages`；`engine/engine_version/retry_count` 取 job 行；`error` 只挂失败阶段（≤500）；
- `progress` 按**固定权重**：`preprocess 10 / detect 30 / recognize 40 / extract 20`，
  按 `metrics.stages` 中 `succeeded` 累加；`ocr_status=succeeded` → 强制 100；
- 无 `metrics` 的历史作业：`succeeded → 100`，其余 `0`，`stages` 退化为单条（`job.stage/status`）；
- 无作业行：`200` + `stages=[]`、`progress=0`、时间为 `null`（不 404：票据存在即合法）。

### 3.5 落库规则（`invoice_fields` / `ocr_lines` / `invoices`）

| 表 | 规则 |
|---|---|
| `ocr_lines` | 同一事务内 `DELETE WHERE invoice_id` → 批量 insert（重跑覆盖不追加，靠 `(invoice_id,page_no,line_no)` 兜底）；`score` 转 `Decimal(str(round(x,3)))`；`box_json` 存四点坐标 |
| `invoice_fields` | 同一事务内先删除 `source <> 'manual' AND confirmed = false` 的旧行再插入；`source='manual'` 或 `confirmed=true` 的行**保留不覆盖**（S3-3/S3-6 的前置保障）；`field_name` 与模板 `field_specs_json` 键一致；`normalized_value=field.value`；`confidence` 取 `field.confidence`；`bbox_json=field.bbox`（`[x1,y1,x2,y2]`）；`source='ocr'`；`source_page_no` 为命中页；`source_line_no` 为页内行号；`rule_id` 由 `(template_id, field_name)` 回查 `field_rules.id`（回退 YAML 时为 `NULL`）；`conflict`/`conflict_value` 取自抽取冲突候选；`need_review = confidence < 0.85 或校验 error`（SCHEMA 既定阈值；S3-2 再统一公式） |
| `invoices` | `invoice_type`、`template_id`、`quality_flag`、`ocr_status='succeeded'`，并把 `invoice_code/invoice_no/invoice_date/buyer_name/buyer_tax_no/seller_name/seller_tax_no/amount/tax_amount/total_amount/check_code` 回填（金额 `Decimal`，日期 `date`）；更新限定 `WHERE ocr_status IN ('pending','running')`，避免覆盖人工干预后的状态 |
| `(invoice_code, invoice_no)` 唯一索引冲突 | 用 `SAVEPOINT` 单独回滚 invoices 更新，**不使作业失败**，记 `metrics.duplicate_invoice_no=true`；强重复的判定与返回交 S3-7 |

### 3.6 提交语义（对 SCHEMA §3.5 的**受控扩展**，记 D-055）

豁免从"只有 `ocr_jobs`"扩到**两处**，理由都是"阶段进度必须进行中可见"：
1. `ocr_jobs`（既有豁免）：每次阶段完成写 `stage`/`metrics` 并 commit；
2. `invoices.ocr_status = 'running'`：一次 UPDATE + commit。
业务数据（`ocr_lines`/`invoice_fields`/`invoices` 其余列）**只在最后一次事务里提交**，失败一律回滚——
不会出现"识别失败但字段半截落库"。

### 3.7 错误、超时与幂等

| 场景 | 行为 |
|---|---|
| 任意异常 | 先 `db.rollback()` → 重取 job → 写 `stage=当前阶段`、`metrics`（该阶段 `failed`）→ commit → 原样抛出，由 `job_service` 分类：可重试 → `pending` + 5s/30s 退避；否则 `failed` + `error`（`type: msg` ≤500，不含明文） |
| 超时 | ARQ `job_timeout=180`（既有）+ 流水线**协作式 deadline** `ocr_pipeline_deadline_seconds=165`：阶段边界检查，超时抛 `TimeoutError`（可重试）。**不引入** `wait_for` 硬杀线程——`to_thread` 无法真正中断，硬杀会留下"线程还在写、状态已被标失败"的竞态；真正的兜底是 ARQ 超时 + `recover_stale_jobs`，如实写进论文"不足" |
| 同 job 重复投递 | `job_service` 的终态跳过 + Redis `SET NX EX 600`（既有） |
| 僵尸线程覆写（ARQ 超时杀不掉的遗留线程） | `job_service` 在写终态前**重读作业行**，校验 `status=='running' AND retry_count==attempt`；不等 → `db.rollback()` 撤掉业务写入、返回 `superseded`（不覆盖新一轮状态）。`_persist_failure` 同样加这道校验 |
| AbortAttempt（作业已不在 running） | 流水线入口发现 `status != running` 抛 `AbortAttempt`；`job_service` 视作 `superseded`：回滚、不改状态、不重试 |
| 对象缺失 | `storage.get_bytes` 抛 `ServiceUnavailableError`（可重试；重试耗尽后 failed，`error` 可定位） |
| 空识别（0 行） | 不失败：`ocr_lines` 空、字段空、`invoice_type=unknown`、`ocr_status=succeeded`、`metrics.counts.lines=0`；详情页字段表为空——这是"识别到了但没有文本"，与"引擎崩了"必须区分 |
| 引擎契约不符 | `OcrContractError` 原样抛出（不可重试，直达 failed），提示检查 paddleocr 版本 |

### 3.8 worker 并发

`WorkerSettings.max_jobs` 默认 **1**（可配 `OCR_WORKER_MAX_JOBS`，上限 2）。理由：PaddleOCR 模型
非线程安全（AGENTS §5），且 CPU 推理单进程吃满；并发 2 仅用于"IO 与推理重叠"的实验对照。

### 3.9 前端（最小改动）

- `types/invoice.ts`：新增 `OcrStage` / `OcrStatusData`；`LatestJob` 可保留；
- `api/invoices.ts::waitForOcr`：改用 `GET /invoices/{id}/ocr-status`，回调签名改为 `OcrStatusData`，
  终态判定用 `ocr_status`；退避与总超时不变（3s→15s，120s）；
- `views/Upload.vue`：识别中显示 `识别中… ${progress}%`，失败取 `stages` 里失败阶段的 `error`；
- 文案同步去掉"轮询用详情接口"的注释（S2-10/S2-11 遗留）；
- 验证：`npm run typecheck && npm run build` + 上传页冒烟（不新增测试框架）。

## 4. 与任务卡/既有决策的冲突（报告，不静默选边）

| # | 冲突 | 处理 |
|---|---|---|
| 1 | 任务卡说 PDF 用 `pypdfium2` 转图 | **不引入**：S1 已锁 PyMuPDF（`ocr/intake.py`），同一能力不装第二个 PDF 引擎（AGENTS §2.1）；"限前 3 页"照样实现（`prepare_pages(max_pages=3)`） |
| 2 | 任务卡要求 `OcrResult{lines,fields,ticket_type,quality_flag,timings}` 统一出口 | 现有 `ocr/types.py` 已拆成 `OcrResult`/`ExtractionResult`（S1-3/S1-5 契约），**不合并**；`ocr_pipeline` 内部用 `PipelineResult`（dataclass）承载"整单结果 + timings"，Pydantic 化与 JSON Schema 导出留 S3-4 |
| 3 | 任务卡把 `timings` 写 `ocr_jobs.metrics`（列不存在） | 见 G1：加列 |
| 4 | SCHEMA §3.5 "每阶段一条记录" | 见 G2：一 run 一行 + `metrics.stages`，SCHEMA 措辞同步改 |
| 5 | 任务卡要求 `invoices.ocr_status` 推进（PROGRESS 遗留 #3） | 本卡落实：pending→running→succeeded/failed；`extract_status` 留 `raw`（S3-2） |
| 6 | `invoice_fields.field_value` 契约是"票面原文形态（¥1,234.56）"，但 `ExtractedField` 只暴露归一值与整行 `raw_text` | S3-1 **暂存归一值**（`field_value = normalized_value`），原文在 `raw_text` 里可核对；S3-4 给 `ExtractedField` 补 `raw_value` 后一次改齐，PROGRESS 记遗留 |
| 7 | `invoice_fields.rule_id` 是 `BIGINT` FK，抽取器给的是字符串 `rule_id` | 用 `(template_id, field_name)` 回查 `field_rules.id`；YAML 回退路径写 `NULL`（不塞字符串进 BIGINT） |
| 8 | S2-7 spec §2 曾明确"不新增 `started_at/finished_at`" | 见 §3.3：协议变了（`ocr-status` 需要真时间），显式加列并记 D-056 |
| 9 | SCHEMA 说"除 `ocr_jobs` 外任何表不得中途提交" | §3.6 受控扩展到 `invoices.ocr_status` 一列，记 D-055 |
| 10 | 模板 `field_rules` 表（DB）与 YAML `field_rules.yaml` 两套规则来源 | 抽取规格**以 DB active 模板的 `field_specs_json` 为准**（S2-8 既定方向，与 `/templates/{id}/test` 同源）；DB 缺该票种才回退 YAML 并记 `rules_source=yaml`。版式/分类关键字仍用 YAML（`regions`/`excerpt_keywords`/权重 DB 没有，PROGRESS 遗留 #36 的解法） |

## 5. 测试设计

**单元（默认 `pytest`，不加载模型、不连 MinIO/Redis/DB）**
- 纯函数：`_merge_page_fields`（首页优先 / 只补空 / `page_conflict` 记录）、`_invoice_values`（金额→`Decimal`、
  日期→`date`、空值不写）、`_line_rows`/`_field_rows`（`bbox_json` 两点框、`score` 三位小数）、
  `_progress`（0/10/80/100、失败按已完成累加）；
- `intake.prepare_pages`：PDF 3 页 → 3 张、`max_pages=2` → 2 张、照片 → 1 张、坏 PDF → 异常；
  `prepare_input` 行为不回归（既有 `test_intake.py` 全绿）；
- `storage.get_bytes`：正常/超限/对象缺失（假 MinIO client）；
- `extractor.rules_from_specs`：与 `load_field_rules()` 对同一份 YAML 生成的规则**逐字段等价**
  （防两套规则来源漂移）；
- `ocr_status_service._build_stages/_progress`：无 metrics 退化、无 job 行空结构、失败阶段 error 只挂一处。

**集成（`-m integration`，临时库 + 假 engine / 假 storage / 假 intake）**
- `run_pipeline` happy path：写 `ocr_lines` / `invoice_fields` / `invoices` 冗余列，`Decimal` 金额、状态推进；
- PDF 多页合并（首页优先、冲突不覆盖、`page_no` 正确）；
- 空识别（0 行）不失败；`is_cipher=true` → 明确"待 S4"错误且不可重试；
- 对象缺失 → 可重试错误；`OcrContractError` → 不可重试；deadline 超时 → 可重试；
- 僵尸 attempt：终态写入前校验 `(status, retry_count)` 失败 → 返回 `superseded`，业务表零写入、状态不变；
- 临时目录清理（返回后目录内无残留）；
- 同一票号冲突（savepoint 回滚 invoices 更新 + `metrics.duplicate_invoice_no`，作业仍成功）；
- 人工字段保护（`source='manual'` / `confirmed=true` 的行不被覆盖）；
- 上传 → 作业 → 详情链路：`invoice.ocr_status=succeeded`、`ocr_fields`/`ocr_lines` 可见；
- `ocr-status` 中途可读（假引擎在第二阶段阻塞，另一会话读到 `progress=10`）、失败路径无半截业务数据。

**真实验收（手工/脚本，CPU）**
- `scripts/ocr_acceptance.py --samples 20`：真实 API + worker，20 张合成票据串行上传，
  轮询到终态，统计成功率、单张总耗时 P50/P95、失败原因分布、临时目录残留；`--cleanup` 清理数据与对象。

## 6. 验收命令与指标

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic upgrade head          # head=0004
../.venv/Scripts/python.exe -m alembic check                 # 零差异
../.venv/Scripts/python.exe -m pytest -q                     # 全绿（新增 ≥20 条）
../.venv/Scripts/python.exe -m pytest -m integration -q      # 全绿
# 真实链路（三个终端）：uvicorn :8000 / arq worker / 验收脚本
../.venv/Scripts/python.exe ../scripts/ocr_acceptance.py --samples 20
# 前端
cd ../web && npm run typecheck && npm run build
```

**验收指标（任务卡）**：上传 20 张成功 ≥18；失败项 `ocr_jobs.error` 能定位原因；单张 P95 < 30s（CPU）；
临时目录无残留；上传 → worker → `invoice_fields` 落库 → 详情页可见。

## 7. 风险与回滚

| 风险 | 处置 |
|---|---|
| DB 模板 `field_specs_json` 过期/缺失导致抽取变差 | DB 缺票种回退 YAML；验收前跑一次 `scripts/seed_templates.py`（幂等）；`rules_source` 写进 metrics 便于定位 |
| 多页 PDF 处理放大耗时 | `max_pages=3` + 协作式 deadline；真实票据多为单页，P95 预算 30s 足够 |
| 迁移影响既有 341 条测试 | 0004 只加可空列；`alembic check` + 全量回归把关；不行则回退 0004（不修改历史迁移） |
| 前端换接口导致上传页轮询异常 | `waitForOcr` 保留退避与总超时；`typecheck/build` + 上传页冒烟；接口异常按"网络抖动继续重试"处理 |
| worker 并发把 CPU 打死 | `max_jobs=1`（默认），并发上限 2 写死在配置校验里 |

## 8. 收口清单

- `PROGRESS` 勾 S3-1 + 实测数字与遗留（#35/#36/#42 关闭，#6 新增 field_value 待 S3-4）；
- `DECISIONS` 新增：D-055（`ocr_jobs` 一 run 一行 + 中途提交扩展）、D-056（加三列与理由）、
  D-057（PDF 复用 PyMuPDF/前 3 页 + 规则来源 DB 优先 YAML 回退 + worker 并发 1）；
- `SCHEMA` §3.5（列 + 一 run 一行 + 提交语义扩展）、`API.md` §2（`ocr-status` 标记已实现、
  `stages` 的 detect 无法单独计时说明）；
- `AGENTS` §10.8 回填"OCR 流水线（S3-1）"锚点一行；
- `.env.example` 补 `OCR_PIPELINE_DEADLINE_SECONDS` / `OCR_PDF_MAX_PAGES` / `OCR_WORKER_MAX_JOBS`；
- `HANDOFF` 更新下一步 S3-2；提交 `feat(s3-1): ...`（**push 由用户执行**）。