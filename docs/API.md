# API.md — 接口契约

> **来源**：《OCR票据识别与加密系统-毕业设计完整方案》第 3 章（3.2 功能清单 A–G、3.4 关键业务规则）、第 6 章（6.3 抽取输出、6.4 人工确认、6.5 密码模块、6.6 状态机）、附录 B（关键接口草案）、附录 C1（统一响应与错误码）；字段名以 `docs/SCHEMA.md` 为准（已生成，冲突时以 SCHEMA.md 为唯一依据）。
> **本文件是前后端的共同语言**：接口变更必须先改这里、再写代码；与本文件冲突的实现视为 bug。
> **状态**：**S0-3 草案（待定项已清零）**。S0-3 把上一版全部 `TODO 待定` 逐条拍板，剩余 3 项实现细节见 §12.2；文中任务号（S2-2 / S3-x / S4-x / S5-x / S6-x）出自《AI协作开发实施计划》。

## 0. 全局约定

- **Base path**：`/api/v1`；另有 `GET /health`（**无前缀**，探针用）。
- **命名**：路径、query、请求体、响应字段一律 `snake_case`，与数据库列名一致（**定死**）。
- **统一响应壳**：`{code, message, data, trace_id}`；成功 `code=0`、`message="ok"`；失败 `data` 为 `null` 或结构化错误详情（见各接口）。示例：`{"code":0,"message":"ok","data":{},"trace_id":"b7c2e1d4f0a9"}`。
- **trace_id**：响应头 `X-Trace-Id` 必返；请求头可传同名值续链；未传则服务端生成 uuid4。
- **认证**：`Authorization: Bearer <jwt>`（S2-2 交付）。载荷 `{sub, role, jti, iat, exp}`，HS256，access 默认 30min；`role` 只用于快速判断与日志，**以数据库为准**。
- **角色只有三个**：`submitter`（报销人）/ `reviewer`（财务审核人）/ `admin`（系统管理员）。不存在的角色名不得出现在代码与文档中。
- **权限矩阵**（S2-3 定稿）：

| 能力 | submitter | reviewer | admin |
|---|---|---|---|
| 上传票据 | 仅本人 | 403 | 404 |
| 查看票据 | 仅本人 | 全部可读 | 不继承，越权即 404 |
| 修正字段 | 本人、非金额字段 | 全部、含金额字段 | 404 |
| 报销单 | 本人建/改/签/提交 | 只读已提交 | 404 |
| 财务待审 / 验签 / 解密 / 审核 | 403 | 全部 | 403（**不得代审**） |
| 票种模板 | 403 | 只读 | 读写 |
| 证书 | 仅本人证书 | 仅本人证书 | 全部 |
| 审计 / 评测 | 403 | 403 | 全部 |

- **数据隔离**：所有查询隐式带 `uploader_id = current_user.id`（`reviewer` 审核场景除外）。**资源级越权**（资源存在但非本人）统一返回 `404/40400`（不泄露资源是否存在），并写一条 `action=forbidden` 的审计事件；**角色级拒绝**（该接口不允许该角色）用 `403/40301`。
- **分页**：列表接口统一 query `page`（默认 1，≥1）+ `page_size`（默认 20，上限 100）；响应 `data = {items, total, page, page_size}`；越界/非整数 → `422/42200`；超过末页返回空 `items`，`total` 不变。
- **排序**：`sort_by`（各接口白名单）+ `order`（`asc`/`desc`，默认 `desc`）；非白名单字段 → `422/42200` 并列出可选值。
- **异步约定**：提交类接口返回 **202** + `job_id`/`run_id`；客户端每 1–2s 轮询 `GET /invoices/{id}/ocr-status`（或对应任务接口），终态 `succeeded`/`failed` 后停止；本阶段不做 SSE/WebSocket。
- **金额与时间**：金额一律 `NUMERIC(12,2)`，JSON 中为**两位小数字符串**（`"1234.56"`），禁止浮点；时间一律 ISO-8601 UTC（`2026-11-18T08:30:00Z`）。
- **安全红线**：任何响应、日志、审计 `detail_json` 中不得出现私钥、口令、明文会话密钥（CEK）、明文影像内容、MinIO 对象名或任何明文存储路径；影像只经 `GET /invoices/{id}/image` 受控流出。

## 1. 认证（S2-2）

> 五条接口均套统一壳。注册仅产生 `submitter`；`reviewer` 由 `admin` 在后台指定，`admin` 由初始化脚本创建，**均不支持自助注册**。
> 会话形状已定稿（D-016）：`access_token` 30 分钟 + `refresh_token` 7 天；`refresh_token` 存 Redis 白名单（key `auth:refresh:{jti}`），`logout` 删白名单即失效。不做无状态黑名单（登出必须真的能吊销）。

### POST /api/v1/auth/register
- 权限：公开 ｜ 类型：同步 ｜ 幂等：否 ｜ 实现：S2-2
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| username | string | 是 | 3–64 字符，不含空白；入库前 trim + 转小写 |
| password | string | 是 | 8–128 字符；只存 Argon2id 哈希 |
| real_name | string | 是 | 1–50 字符（trim 后非空） |
| dept | string | 否 | ≤50 字符 |

- 出参：**201**，`data` = UserOut：`{id, username, real_name, dept, role, status, created_at}`（无 `password_hash`）
- 错误：`422/42200` 参数校验失败；`409/40900` 用户名已存在
- 备注：用户名不区分大小写；服务端固定创建 `role=submitter`，请求体不接受角色字段

### POST /api/v1/auth/login
- 权限：公开 ｜ 类型：同步 ｜ 幂等：是（无副作用；同一账号允许多端登录，各自持有独立 `jti`）｜ 实现：S2-2
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| username | string | 是 | 3–64 字符，trim + 小写后匹配 |
| password | string | 是 | 8–128 字符 |

- 出参：**200**，`data` = `{access_token, token_type: "bearer", expires_in: 1800, refresh_token, refresh_expires_in: 604800, user: UserOut}`
- 错误：`401/40101` 用户名或密码错误（账号不存在与密码错误**同码同文案**，不泄露账号是否存在）；`403/40301` 账号已 `disabled` / `locked`
- 备注：`refresh_token` 存 Redis 白名单；登录失败计入审计 `action=login_failed`（`detail_json` 只记用户名哈希，不记口令）

### GET /api/v1/auth/me
- 权限：登录用户（三角色）｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S2-2
- 入参：无
- 出参：**200**，`data` = UserOut（同上）
- 错误：`401/40101` 未提供凭据 / 令牌过期 / 用户已禁用；`404/40400` 用户不存在
- 备注：按 `sub` 回表取用户，**不信 token 内的 `role`**

### POST /api/v1/auth/refresh
- 权限：持有有效 `refresh_token` ｜ 类型：同步 ｜ 幂等：否（每次轮换，旧 `jti` 作废）｜ 实现：S2-2
- 入参（JSON）：`{refresh_token}`
- 出参：**200**，`data` = `{access_token, token_type: "bearer", expires_in: 1800, refresh_token, refresh_expires_in: 604800}`
- 错误：`401/40101` 令牌过期 / `jti` 不在白名单 / 已被轮换
- 备注：**轮换式刷新** —— 成功即删旧 `jti`、写新 `jti`，令牌被窃取后只能“抢用一次”

### POST /api/v1/auth/logout
- 权限：登录用户 ｜ 类型：同步 ｜ 幂等：是（重复登出仍返回 200）｜ 实现：S2-2
- 入参（JSON）：`{refresh_token}`（可选）
- 出参：**200**，`data` = `{"logged_out": true}`
- 错误：`401/40101` 未登录
- 备注：删 Redis 白名单中的 `jti`；写审计 `action=logout`

## 2. 票据上传与识别（S2-5 / S2-6 / S3-x）

> 生命周期见方案 6.6，落库拆成两列，**不要混用**：票据业务状态 `status` 只有 `draft → confirmed → in_report → archived`（`void` 为软删终态）；识别状态 `ocr_status` 为 `pending → running → succeeded|failed`；"待确认"落在 `extract_status=need_confirm`，不是 `status` 值。
> 上传链路顺序**定死**：`sha256 查重 → 内存中信封加密 → 写 MinIO → 落库 → 投递异步任务`。

### POST /api/v1/invoices/upload
- 权限：`submitter`（本人）｜ 类型：上传 + 异步 ｜ 幂等：否（按 `sha256` 去重）｜ 实现：S2-6 上传 / S3-1 识别
- 入参（`multipart/form-data`）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| file | file | 是 | `image/jpeg`·`image/png`·`application/pdf`，**≤20MB**（D-018；单一来源为配置 `MAX_UPLOAD_MB=20`）；MIME 白名单 + 魔数双校验；PDF ≤5 页 |

- 出参：**202**，`data`：

```json
{"invoice_id": 101, "file_id": 88, "job_id": 501, "sha256": "9f2c...",
 "status": "draft", "ocr_status": "pending",
 "dedup": {"hit": false, "invoice_id": null}}
```

- 错误：`409/40901` 重复影像（**本人/审核人** → `data.invoice_id` 为既有票据，客户端直接跳详情；**跨账号** → `data.invoice_id=null`，文案“该影像已被其他账号上传，不能重复提交”，不泄露他人的票据）；`413/41300` 超大小上限；`422/42200` 类型/魔数不符、PDF 页数超限、空文件；`500/50000` 存储或队列不可用
- 备注：**响应不得出现 MinIO 对象名或明文路径**；明文只在内存中存在一次，信封加密后才落盘（收方为财务服务器证书）；入库失败必须补偿删除已写对象；重复命中不再写对象存储、不再投任务

### POST /api/v1/invoices/batch
- 权限：`submitter`（本人）；`reviewer` → 403；`admin` → 404 ｜ 类型：同步建单、**异步识别** ｜ 幂等：否 ｜ 实现：S3-8 ✅
- 入参（multipart）：`files`（1–30 个，单文件上限同上传接口）
- 出参：**202**，`data` = `{batch_id, created, duplicates, rejected, items: [{filename, status, invoice_id, job_id, error}]}`，
  `status ∈ created|duplicate|rejected`
- 错误：`409/40901` 仅当整批只有去重项？——**不整批失败**，去重/非法文件按项返回；`422/42200` 文件数越界；
  `503/50300` 队列不可用（已建单保留，可重试）
- 备注：接口**不做同步识别**（避免 Nginx 60s）；单用户"已入队"识别任务 ≤ `OCR_MAX_ACTIVE_PER_USER`（默认 3），
  其余服务端排队（`ocr_jobs.dispatched=false`），由 worker 结束钩子/轮询补位；去重项只在本次响应出现

### GET /api/v1/invoices/batch/{batch_id}
- 权限：本人，或 `reviewer`；`admin` → 404 ｜ 类型：同步（轮询）｜ 幂等：是 ｜ 实现：S3-8 ✅
- 入参：无
- 出参：**200**，`data` = `{batch_id, total, counts: {pending, running, succeeded, failed}, items: [{invoice_id, filename, ocr_status, extract_status, progress, error}], created_at}`
- 错误：`404/40400`（批次不存在/无权）；`422/42200` `batch_id` 非法 UUID
- 备注：`progress` 与 `GET /invoices/{id}/ocr-status` 同一算法；轮询会顺带补位排队作业；建议 2–3s 一次，全部终态即停

### POST /api/v1/invoices/batch/{batch_id}/items/{invoice_id}/retry
- 权限：本人（`reviewer`/`admin` → 404）｜ 类型：同步建作业、异步识别 ｜ 幂等：否 ｜ 实现：S3-8 ✅
- 入参：无
- 出参：**202**，`data` = `{invoice_id, job_id}`
- 错误：`404/40400`（票据不属于该批/无权）；`409/40900` 该票据已有进行中的识别任务
- 备注：**用原 `invoice_files` 重投，不新建 invoice/file**（任务卡硬要求）；旧 job 保留可追溯

### GET /api/v1/invoices
- 权限：`submitter`（仅本人）；`reviewer` 可列全部；`admin` → 404 ｜ 类型：同步（分页）｜ 幂等：是 ｜ 实现：S2-5
- 入参（query）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| status | string | 否 | `draft`/`confirmed`/`in_report`/`archived`/`void` |
| extract_status | string | 否 | `raw`/`need_confirm`/`confirmed` |
| invoice_type | string | 否 | 模板 `type_code` |
| q | string | 否 | 发票号/销售方名称子串，`%`/`_` 已转义 |
| date_from / date_to | string | 否 | ISO-8601 日期，闭区间 |
| review_status | string | 否 | `pending`（已确认未复核）｜`reviewed`（已复核）；S3-9 |
| page / page_size | int | 否 | 默认 1 / 20，上限 100 |
| sort_by | string | 否 | `created_at`｜`invoice_date`｜`total_amount`（白名单） |
| order | string | 否 | `asc`｜`desc`，默认 `desc` |

- 出参：**200**，`data = {items: InvoiceOut[], total, page, page_size}`（列表不返回 `ocr_lines`）
- 错误：`422/42200` 分页/排序/时间格式非法；`401/40101` 未登录
- 备注：软删票据默认不返回；`reviewer` 无 `uploader_id` 过滤，但仍受分页协议约束

### GET /api/v1/invoices/{id}
- 权限：本人，或 `reviewer`（审核需要）；`admin` → 404 ｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S2-5
- 入参：路径参数 `id`（int）
- 出参：**200**，`data` = `{invoice: InvoiceOut, fields: InvoiceFieldOut[], latest_job: {job_id, stage, status, elapsed_ms, retry_count, error}}`
- 错误：`404/40400` 不存在或无权访问（**不区分**）
- 备注：详情不返回影像二进制；预览走 `GET /invoices/{id}/image`。
  `fields[].review_level` 为服务端派生（`auto`≥0.90 / `review` 0.60–0.90 / `manual`<0.60），**不落库**；
  前端颜色与文案以它为准，不要自己硬编码阈值（S3-2/D-058）。

### GET /api/v1/invoices/{id}/ocr-status
- 权限：本人，或 `reviewer`；`admin` → 404 ｜ 类型：同步（轮询）｜ 幂等：是 ｜ 实现：S3-1 ✅（S2-7 提供作业框架）
- 入参：无
- 出参：**200**，`data` = `{invoice_id, ocr_status, progress, stages: [{stage, status, elapsed_ms, note, engine, engine_version, retry_count, error}], started_at, finished_at}`

```json
{"invoice_id": 101, "ocr_status": "running", "progress": 80,
 "stages": [{"stage": "preprocess", "status": "succeeded", "elapsed_ms": 420, "note": null,
             "engine": "paddleocr", "engine_version": "2.7.3", "retry_count": 1, "error": null},
            {"stage": "detect", "status": "succeeded", "elapsed_ms": null,
             "note": "DBNet 与 SVTR 在同一次 PaddleOCR 调用内完成，无法单独计时", "...": "..."}],
 "started_at": "2026-11-18T08:30:01Z", "finished_at": null}
```

- 错误：`404/40400` 不存在或无权访问
- 备注：`stage ∈ {preprocess, detect, recognize, extract}`；`progress` 按阶段**固定权重**累加：`preprocess 10 / detect 30 / recognize 40 / extract 20`（与《完整方案》7.7 的耗时预算同比例，不做按图动态加权——前端只需要一个能解释的进度条），`succeeded` 即 100；
  `detect` 与 `recognize` 是 PaddleOCR 的同一次调用，两者一起完成，故 `detect.elapsed_ms` 恒为 `null`；
  `stages` 来自 `ocr_jobs.metrics.stages`，S3-1 之前的作业没有 metrics 时退化为单条（`stage/status/elapsed_ms` 取作业行）；
  `error` 只挂在失败阶段；失败重试上限 3 次；建议轮询间隔 1–2s、总时长 ≤5min

### GET /api/v1/invoices/{id}/fields
- 权限：本人，或 `reviewer`；`admin` → 404 ｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S3-4 ✅
- 入参：无
- 出参：**200**，`data` = `{invoice_id, overall_confidence, needs_confirm: [field_name], conflicts: [{field_name, reason, expected, actual}], items: InvoiceFieldOut[]}`

```json
{"invoice_id": 101, "overall_confidence": "0.890", "needs_confirm": ["buyer_tax_no"],
 "conflicts": [{"field_name": "total_amount", "reason": "candidate_conflict",
                "expected": "￥1,234.56", "actual": "1200.00"}],
 "items": [{"field_name": "total_amount", "field_value": "￥1,234.56",
            "normalized_value": "1234.56", "confidence": 0.98,
            "bbox": [700, 610, 900, 640], "source": "ocr",
            "need_review": false, "conflict": true, "review_level": "auto"}]}
```

- 错误：`404/40400`
- 备注：`items` 为本次抽到的字段（白名单 13 个，含大写金额 `total_amount_upper`）；`field_value` 是**票面原文**（如 `￥1,234.56`）、`normalized_value` 是**归一值**（如 `1234.56`）；
  `need_review = level != auto`（S3-2 三档），`review_level` 由服务端派生；冲突**不静默修正**，`conflict=true` 且保留 `conflict_value`；
  `conflicts[].reason` 当前只有 `candidate_conflict`（抽取阶段的同优先级多候选）——`money_sum_mismatch` 等校验原因
  在确认接口（S3-6）返回，避免两处规则各算一套

### PATCH /api/v1/invoices/{id}/fields
- 权限：本人（`submitter`，**全部字段**）；`reviewer`（全部字段；二审前也可改）；`admin` → 404 ｜ 类型：同步 ｜ 幂等：否 ｜ 实现：S2-5 / S3-9
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| version | int | 是 | 乐观锁版本（`invoices.version`）；与库内不一致 → `409/40900` |
| fields | array | 是 | 1–12 项；`field_name` 必须在票据字段集内 |
| fields[].field_name | string | 是 | 如 `total_amount`、`buyer_tax_no` |
| fields[].value | string | 是 | 文本值；金额按两位小数存储 |
| fields[].confirm | bool | 否 | 人工确认标记，默认 `false` |
| confirm_amount | bool | 条件必填 | 修改**金额类字段**（`amount` / `tax_amount` / `total_amount`）时必须传 `true`，否则 `422/42202` |

- 出参：**200**，`data` = `{invoice_id, version, status, updated: [{field_name, before, after}], needs_confirm, conflicts}`
- 错误：`404/40400`；`409/40900` 版本冲突 / 状态不允许 / **已复核锁定**；`422/42200` 字段名非法、值格式非法；`422/42201` 修改后仍存在未决冲突
- 备注：`source` 由服务端置 `manual`；**权限矩阵（S3-9/D-068，取代 D-017 的金额限制）**：
  `submitter` 可改**全部字段**（含金额）；`reviewer` 可改全部字段，且**二审前（status=confirmed 未复核）仍可改**；
  **任何人改金额都必须带 `confirm_amount=true`**（否则 `422/42202`）；`admin` 一律 404；
  `status=draft` 且 `extract_status ∈ {raw, need_confirm}` 可改；`reviewed_at` 非空 → `409`"已复核，不可修改"。
  审计行（`action=confirm_field`）仍留 S4-7 哈希链一起写（D-065）

### POST /api/v1/invoices/{id}/reocr
- 权限：本人（`reviewer` 触发权限与 S3-6 一起定）｜ 类型：异步（202）｜ 幂等：否（有进行中作业时 409）｜ 实现：S3-3 ✅（**前端入口已移除**，D-070；接口保留）
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| fields | array | 是 | 1–6 个、去重；必须是该票据已抽到的字段，且不是 `manual`/`confirmed`/已 `ocr_retry`，有 `bbox` 与来源行 |
| version | int | 否 | 传入则校验乐观锁 |

- 出参：**202**，`data` = `{invoice_id, job_id, fields}`
- 错误：`404/40400`；`403/40301` 非本人；`409/40900` 状态不允许（非 `draft` 或 `extract_status` 非 raw/need_confirm）、版本冲突、已有进行中的重识别；`422/42200` 字段数越界/重复、字段不存在、人工值/已重识别、无 bbox 或来源行
- 备注：worker 按 `invoice_fields.source_page_no + source_line_no` 回查 `ocr_lines.box_json`（校正图四点）→
  外扩 `max(8px, 10%×行高)` → clamp → `INTER_CUBIC` 放大 2×（长边 1024）→ `recognize_line`；
  **仅当新分 > 旧 confidence 时**更新 `raw_text/confidence/source=ocr_retry`，**`field_value` 不变**（改值走人工确认）；
  随后重算 `overall_confidence` 与 `extract_status`；单张总增量 ≤1.5s，超预算字段记 `skipped`；
  逐字段 old/new 明细写 `ocr_jobs.metrics.fields[]`；识别为空（非文本区域）记 `skipped:no_text`，不使整单失败

### GET /api/v1/invoices/{id}/duplicate-check
- 权限：本人或 `reviewer`；`admin` → 404 ｜ 类型：同步（只读）｜ 幂等：是 ｜ 实现：S3-7 ✅
- 入参：无
- 出参：**200**，`data` = `{invoice_id, level: "strong"|"weak"|"none", matches: [{invoice_id, kind, invoice_code, invoice_no, invoice_date, total_amount, status, created_at}]}`

```json
{"invoice_id": 102, "level": "weak",
 "matches": [{"invoice_id": 87, "kind": "weak", "invoice_code": null, "invoice_no": "05975405",
              "invoice_date": "2025-01-02", "total_amount": "1850.26",
              "status": "confirmed", "created_at": "2026-11-20T06:30:00Z"}]}
```

- 错误：`404/40400`
- 备注：`kind`：`invoice_no`（`duplicate_of` 或同票号）/ `image`（同 sha256，纵深防御）/ `weak`
  （同 `total_amount`+`invoice_date`+`seller_tax_no`，票号不同）；强重复时 `POST /confirm` 返回 `409`；
  `matches` 只给最小字段（不含姓名/税号明文）；弱重复**只提示不阻断**（D-066 覆盖 D-013）

### POST /api/v1/invoices/{id}/confirm
- 权限：本人（`reviewer` → 403，`admin` → 404）｜ 类型：同步 ｜ 幂等：是（已 `confirmed` 重复调用返回当前状态）｜ 实现：S3-6 ✅
- 入参：无（可选 `version`，见备注）

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| version | int | 否 | 传入则做乐观锁校验 |

- 出参：**200**，`data` = InvoiceOut（`status=confirmed`、`extract_status=confirmed`）
- 错误：`404/40400`；`409/40900` 状态不允许；`422/42201` 整单校验未通过，`data.fields = [{field_name, error}]` 逐字段列出
- 备注：**整单确认闸门**——服务端重跑 validators（必填齐备 + 无未决冲突），**不信前端传来的"已校验"标记**；
  闸门顺序固定：必填缺失 → 未决冲突 → validators 错误，同一字段只留第一条；`data.fields` 逐条给出
  `{field_name, error}`；通过后**把全部字段置 `confirmed=true, need_review=false`**（整单确认即已核对全部字段，D-070），
  `status` 与 `extract_status` 同事务置 `confirmed`、`version += 1`；不再要求逐字段确认；
  `confirm_field` 审计行待 S4-7 哈希链一起写（S3-6/D-065）

### POST /api/v1/invoices/{id}/review
- 权限：`reviewer`（submitter → 403；admin/他人 → 404）｜ 类型：同步 ｜ 幂等：是（已复核返回当前状态）｜ 实现：S3-9 ✅
- 入参：无（可选 `version`，见备注）

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| version | int | 否 | 传入则做乐观锁校验 |

- 出参：**200**，`data` = InvoiceOut（`reviewed_by`、`reviewed_at` 非空；`version+1`）
- 错误：`404/40400`；`409/40900` 票据未整单确认 / 版本冲突；`422/42201` 复核前校验未通过，`data.fields = [{field_name, error}]`
- 备注：**二审闸门**——服务端重跑 `collect_confirm_errors`（必填/未决冲突/低置信未确认/validators），
  只认库内当前值；通过后 `reviewed_by/reviewed_at`、`extract_status=confirmed` 同事务落库；
  `reviewed_at` 非空即**锁定修改**（PATCH 一律 409）；审计行待 S4-7；"待复核" = `status='confirmed' AND reviewed_at IS NULL`

### DELETE /api/v1/invoices/{id}
- 权限：本人（`reviewer`/`admin` → 404）｜ 类型：同步（**硬删除**）｜ 幂等：否（删后再删 → 404）｜ 实现：S3-11/D-071
- 入参：无
- 出参：**204** 无响应体
- 错误：`404/40400`；`409/40900` 非 `draft`（已确认/待复核/已进报销单/已归档）、识别任务进行中、已被报销单引用
- 备注：**物理删除** `invoices + invoice_fields + ocr_lines + ocr_jobs + invoice_files` 并删除 MinIO 对象；
  删除后同一影像可重新上传（sha256 去重记录一并释放）。**不可恢复**，因此仅允许删草稿；
  删除前会把其他票据的 `duplicate_of` 指向本票的引用置空

### GET /api/v1/invoices/{id}/image
- 权限：本人，或 `reviewer`；`admin` → 404 ｜ 类型：下载（二进制流）｜ 幂等：是 ｜ 实现：S3-5 ✅（S4 接入后在此解密）
- 入参：`page`（int，PDF 页码，默认 1）；`variant`（`original`｜`preprocessed`，默认 `original`）
- 出参：**200**，图片/PDF 页字节；响应头 `Content-Type`、`ETag=<sha256>`、`Cache-Control: private, no-store`、`X-Content-Type-Options: nosniff`
- 错误：`404/40400`；`409/40900` 信封缺失或已损坏；`409/50030` MAC 校验失败；`409/50031` 解密失败（均**不返回任何明文片段**）
- 备注：服务端在内存中解信封，响应结束立即释放；**绝不返回 `storage_key`、MinIO 路径或公开签名 URL**。
  **当前实现（S3-5/D-063）**：明文仅当 `STORAGE_ALLOW_PLAINTEXT=true` 且 `invoice_files.is_cipher=false`（本地调试）；
  密文/生产一律 `409/40900`"加密影像解密尚未接入（S4）"，不做明文回退。照片返回原字节；
  PDF 按 `page` 以 **250 DPI** 渲染 PNG（与 S3-1 识别时一致，否则框会按比例全偏）；
  `ETag` = 响应字节 SHA-256；前端带 Bearer 取 blob 再 `objectURL`，不用 `<img src>` 直链。

### GET /api/v1/invoices/{id}/lines
- 权限：本人，或 `reviewer`；`admin` → 404 ｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S3-5 ✅
- 入参：`page`（int，默认 1）
- 出参：**200**，`data` = `{invoice_id, page_no, lines: [{line_no, bbox: [x1,y1,x2,y2], score, field_name}]}`

```json
{"invoice_id": 101, "page_no": 1,
 "lines": [{"line_no": 3, "bbox": [423, 357, 900, 395], "score": 0.973, "field_name": "total_amount"}]}
```

- 错误：`404/40400`；`422/42200` `page` 越界；`409/40900` 加密影像未接入（同 image 接口）
- 备注：`bbox` 是**原图**外接框（服务端已从 `ocr_lines` 校正图四点映射）；`field_name` 为命中该行的字段，
  未命中为 `null`；**不返回行文本**（票面原文只经字段 `raw_text`/`field_value` 暴露，缩小明文面）；
  畸形 `box_json` 的行直接跳过，不返回 `[0,0,0,0]` 假框

## 3. 票种模板与规则（S2-8）

> 写操作仅 `admin`；`reviewer` 只读；`submitter` 一律 403。模板以数据库为准（启动时用 S1 的 YAML/JSON seed），改规则 60s 内热生效、不重启。

### GET /api/v1/templates
- 权限：`reviewer` / `admin`（`submitter` → 403）｜ 类型：同步（分页）｜ 幂等：是 ｜ 实现：S2-8
- 入参：`active`（bool，默认 true）、`type_code`、`page`、`page_size`、`sort_by`（`updated_at`｜`version`｜`type_code`）、`order`
- 出参：**200**，`data = {items: TemplateOut[], total, page, page_size}`
- 错误：`403/40301` 角色不符；`422/42200` 排序字段非白名单
- 备注：默认只列 `active=true` 的生效版本；管理端传 `active=false` 查历史版本；列表不返回完整 `field_specs_json`（详情接口取），避免响应过大

### GET /api/v1/templates/{id}
- 权限：`reviewer` / `admin` ｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S2-8
- 入参：路径参数 `id`
- 出参：**200**，`data` = `{template: TemplateOut（含 field_specs_json）, rules: [{id, field_name, pattern, validator, error_msg, priority, active}]}`
- 错误：`404/40400` 模板不存在
- 备注：`field_specs_json` 是"字段 → 锚定策略/正则/相对位置"的规格；`rules` 来自 `field_rules`（`template_id` 为空的是通用规则）

### POST /api/v1/templates
- 权限：`admin` ｜ 类型：同步 ｜ 幂等：否 ｜ 实现：S2-8
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| type_code | string | 是 | `^[a-z][a-z0-9_]{2,31}$`，全局唯一 |
| type_name | string | 是 | 1–50 字符 |
| match_keywords | array | 是 | 1–10 个非空字符串，用于标题区匹配 |
| field_specs_json | object | 是 | 字段 → 锚定策略；正则长度 ≤500，必须可编译 |
| active | bool | 否 | 默认 `false`（新版本不自动生效） |

- 出参：**201**，`data` = TemplateOut（`version=1`、`active` 按入参）
- 错误：`409/40900` `type_code` 已存在（同票种多版本请用 `PUT /templates/{id}` 升版本）；`422/42200` 正则非法 / 结构不符
- 备注：正则属于不受信输入，必须编译校验并限制长度；同 `type_code` 允许多版本共存，但**至多一版 `active=true`**；把 `active` 置 true 时须同时把旧版本置 false；写审计 `action=template_create`

### PUT /api/v1/templates/{id}
- 权限：`admin` ｜ 类型：同步 ｜ 幂等：否（版本递增）｜ 实现：S2-8
- 入参：同创建接口，另加 `version`（int，必填，当前生效版本号）
- 出参：**200**，`data` = TemplateOut（**生成 `version+1` 的新行并返回新 `id`**；旧版本行保留）
- 错误：`404/40400`；`409/40900` 版本冲突（`data.current_version` 返回最新值）；`422/42200` 正则/结构非法
- 备注：版本只增不改；发布新版本时**复制**当前版本的 `field_rules` 行（`template_id` 指向新版本），只改动差异项——规则通常是微调，重建等于让用户重录一遍；更新后 60s 内生效（服务端缓存 TTL）；同 `type_code` 至多一版 `active=true`（SCHEMA 部分唯一索引兜底）；写审计保留旧版本

### POST /api/v1/templates/{id}/test
- 权限：**仅 `admin`**（`reviewer` 只读，不允许试跑）｜ 类型：同步（上传 + 即时计算）｜ 幂等：是（不落库）｜ 实现：S2-8
- 入参（`multipart/form-data`）：`file`（必填，JPG/PNG，≤20MB）
- 出参：**200**，`data` = `{template_id, type_code, matched_keywords, overall_confidence, elapsed_ms, fields: [{field_name, value, confidence, bbox, source}]}`
- 错误：`404/40400`；`422/42200` 图片非法 / 模板未启用；`500/50010` OCR 引擎异常
- 备注：**不建票据、不投任务、不落 MinIO**，图片只进内存；仅用于调模板；不返回任何存储路径

## 4. 报销单（S5-1 / S5-2）

> 单号 `RPT-YYYYMM-序号` 由 PostgreSQL 序列生成；金额一律 `NUMERIC(12,2)`；状态 `draft → pending_sign → signed → encrypted → submitted → approved|rejected → archived`。
> **S5-1 实现口径（D-084）**：序列 `report_no_seq` 是**全局**的（`RPT-{YYYYMM}-{全局序号:04d}`，业务月按 `Asia/Shanghai`）；序号**不按月重置**，只保证唯一、不保证连续（失败/回滚**不退号**，号段被消耗）；"一张票只能进一张有效单据"由**票据行 `FOR UPDATE` + 状态分诊**在服务层保证（SCHEMA 无跨表约束），绕过服务层直写库不在此保证范围内。

### POST /api/v1/reports
- 权限：`submitter` ｜ 类型：同步 ｜ 幂等：否 ｜ 实现：S5-1
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| title | string | 是 | 1–100 字符 |
| reason | string | 是 | 1–500 字符 |

- 出参：**201**，`data` = ReportOut（`status=draft`、`total_amount="0.00"`、`items=[]`）
- 错误：`422/42200` 参数校验失败；`401/40101` 未登录
- 备注：`total_amount` 不接受客户端传值，由明细汇总；建单写审计 `action=report_create`（同事务，`detail_json` 只含 `report_no`/`status`）；`department` 为派生字段（申请人 `users.dept`），不入参不落库

### POST /api/v1/reports/{id}/items
- 权限：本人 `submitter`（仅 `draft` 或 `rejected`）｜ 类型：同步 ｜ 幂等：否 ｜ 实现：S5-1
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| invoice_id | int | 是 | 必须是本人 `status=confirmed` 的票据 |
| amount | string | 是 | 两位小数，`0 ≤ amount ≤ 票据 total_amount`（允许部分报销，SCHEMA 5.2） |
| remark | string | 否 | ≤200 字符 |

- 出参：**201**，`data` = ReportOut（含 `items` 与重算后的 `total_amount`、`item_count`）
- 错误：`404/40400` 票据不存在或非本人；`409/40900` 票据非 `confirmed` / 单据状态不允许；`409/40901` 票据已被其他有效单据引用（重复报销）；`422/42201` `amount` 超出票面合计（`data.expected`）
- 备注：合计由服务端汇总 `items.amount`，**不信客户端**；同一单内重复添加返回 `409/40901`（`UNIQUE(form_id, invoice_id)`）；跨单引用同一票据由业务层拦截（**票据行 `FOR UPDATE` 串行化 + `status='in_report'` 分诊**，D-084；SCHEMA 不加跨表约束），票据已是 `in_report` 时同样 `409/40901`；添加成功后票据 `status` 置 `in_report`（同一事务，SCHEMA 5.2 跨表不变式）；本动作**不单独写审计**，整单审计在 S5-2 的 sign/seal/submit 闭环

### POST /api/v1/reports/{id}/sign
- 权限：本人 `submitter` ｜ 类型：同步 ｜ 幂等：否（重复签名为重签：旧签名置 `void`、`signatures.version` +1）｜ 实现：S5-2
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| private_key_passphrase | string | 是 | 解锁**本人**签名私钥的口令；只在内存停留，**不落日志、不回显**。口令不能走环境变量——每人一把私钥、口令由本人掌握（若托管到服务端，等于服务端可代签，不可否认性就被削弱了） |

- 出参：**200**，`data`：

```json
{"signature_id": 9, "digest_algo": "SM3", "digest_hex": "66c7f0f4...",
 "algo": "SM2", "signature_b64": "MEUCIQ...", "cert_id": 3,
 "canonical_sha256": "9f2c...", "status": "signed"}
```

- 错误：`404/40400`；`409/40900` 状态不允许（须 `draft`、`pending_sign` 或 `rejected`；明细齐全时服务端自动置 `pending_sign`）、票据未全部确认、无有效证书；`422/42200` 口令错误（**统一文案**，不区分"口令错"与"私钥不可用"）；`500/50020` 签名运算失败
- 备注：签名范围 = 报销单业务字段 + 每张票据 `(invoice_code, invoice_no, amount, file_sha256)` + `nonce` + `created_at`，**不签影像本体**；`canonical_json` 原样落 `report_forms.canonical_json`，验签时直接取用重算、**不重新拼装**；`digest_hex = SM3(canonical_bytes)`，`canonical_sha256` 仅作跨语言快速比对/排障，二者都不是"签名内部 e"；**绝不把私钥或 CEK 写进响应/日志**；重新签名须把旧签名置 `void`

### POST /api/v1/reports/{id}/submit
- 权限：本人 `submitter` ｜ 类型：同步（事务内完成）｜ 幂等：**是**（`idempotency_key` 唯一）｜ 实现：S5-2
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| idempotency_key | string | 是 | 8–64 字符，客户端生成；全局唯一 |
| version | int | 否 | 乐观锁校验 |

- 出参：**200**，`data`：

```json
{"report_id": 12, "report_no": "RPT-202611-0007", "status": "submitted",
 "signature_id": 9, "envelope_id": 5, "submitted_at": "2026-11-18T08:30:00Z", "duplicated": false}
```

- 错误：`409/40900` 单据未签名/状态不允许、同一幂等键但载荷不同（`data.reason="idempotency_key_reused"`）；`422/42200` 缺少幂等键
- 备注：**幂等键必填**；已提交过的同一键 → `200` + `duplicated: true` + **既有单号**（不重复签名、不重复封装）；提交在同一事务内完成 `规范化 → 摘要 → 签名校验 → 信封封装 → 状态推进 → 审计`，失败全回滚；响应不含任何密文与密钥；耗时主要在信封（2MB 影像约数百毫秒），**首版同步**，不返回 202

### GET /api/v1/reports/{id}
- 权限：本人，或 `reviewer`（仅 `submitted` 之后）；`admin` → 404 ｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S5-1 / S5-3
- 入参：无
- 出参：**200**，`data` = `{report: ReportOut, signature: SignatureOut | null, envelope: EnvelopeOut | null, verify_status}`
- 错误：`404/40400` 不存在或无权访问
- 备注：**不返回 `ciphertext_b64`、`wrapped_key_b64`、`ciphertext_storage_key`**；明文影像只经 `decrypt` 的临时预览。S5-1 已按此形状实现：`signature`/`envelope` 在 S5-2 之前恒为 `null`，`verify_status` 无签名时为 `unverified`

### GET /api/v1/reports/mine
- 权限：`submitter`（仅本人）；`reviewer`/`admin` → 403 ｜ 类型：同步（分页）｜ 幂等：是 ｜ 实现：S5-1
- 入参：`status`、`date_from`/`date_to`、`page`、`page_size`、`sort_by`（`created_at`｜`submitted_at`）、`order`
- 出参：**200**，`data = {items: ReportOut[]（不含 items 明细）, total, page, page_size}`
- 错误：`403/40301`；`422/42200` 排序/日期非法
- 备注：列表只给汇总，明细走详情接口，避免 N+1；`sort_by=submitted_at` 时**空值（草稿）排最后**（`NULLS LAST`，D-084）

## 5. 财务审核（S5-3 / S5-4 / S5-5）

> 仅 `reviewer`；`admin` **不得代审**（403）；`submitter` 403。验签/解密失败一律 `409` + 分类原因码，**不返回任何明文**。

### GET /api/v1/finance/pending
- 权限：`reviewer` ｜ 类型：同步（分页）｜ 幂等：是 ｜ 实现：S5-3
- 入参：`verify_status`（`unverified`｜`ok`｜`failed`）、`submitted_from`/`submitted_to`、`q`（单号/申请人）、`page`、`page_size`、`sort_by`（`submitted_at`｜`total_amount`）、`order`
- 出参：**200**，`data = {items: [{report_id, report_no, applicant_id, applicant_name, total_amount, item_count, submitted_at, verify_status}], total, page, page_size}`
- 错误：`403/40301` 角色不符；`422/42200` 排序/日期非法
- 备注：`verify_status` 在提交时算一次落库，**列表不实时验签**；不返回密文全文

### POST /api/v1/finance/reports/{id}/verify
- 权限：`reviewer` ｜ 类型：同步 ｜ 幂等：否（每次执行都写审计）｜ 实现：S5-4
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| （无入参字段） | — | — | **入参为空对象 `{}`**：财务服务器私钥口令走**环境变量** `FINANCE_KEY_PASSPHRASE`（D-016），不经 HTTP 传递、不进日志、不回显 |

- 出参：**200**（全部通过），`data`：

```json
{"report_id": 12, "verified": true,
 "steps": [{"step": "envelope_lookup", "ok": true}, {"step": "mac", "ok": true},
           {"step": "unwrap_cek", "ok": true}, {"step": "decrypt", "ok": true},
           {"step": "cert_check", "ok": true}, {"step": "signature", "ok": true},
           {"step": "digest_recheck", "ok": true}],
 "cert_fp": "3a7d...", "payload_sha256_match": true,
 "verified_at": "2026-11-18T08:31:00Z", "verify_token": "vt_5f1c...", "verify_token_expires_in": 1800}
```

- 错误：**409**，`code` 按失败类别取 C1 业务码（`50030` MAC / `50031` 解密 / `50021` 验签），`data.reason_code ∈ {mac_mismatch, bad_padding, digest_mismatch, bad_signature, cert_invalid}`，`data.failed_step` 指出失败环节；`404/40400`；`422/42200` 口令错误
- 备注：顺序**固定**为 `查信封 → 验 MAC → 解 CEK → 解密 → 校验证书（指纹/有效期/吊销/公钥一致性）→ 验签 → 重算摘要比对`；**先验 MAC 再解密**，MAC 不过不得进入解密；摘要必须从库内原始数据**重算**，不得拿提交时存的摘要直接比；失败不返回明文字段、不返回细节到可利用粒度；`mac_mismatch`/`bad_padding` 只进审计与响应，**不写入 `signatures.verify_result`**（SCHEMA §9 约定）；写审计 `action=verify`

### POST /api/v1/finance/reports/{id}/decrypt
- 权限：`reviewer` ｜ 类型：同步（返回临时预览凭据）｜ 幂等：否（每次签发新预览凭据）｜ 实现：S5-4
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| verify_token | string | 是 | `verify` 通过后签发，30min 有效；未验签/过期 → 409 |

- 出参：**200**，`data` = `{preview_token, expires_at, ttl_seconds: 1800, content_type, size, fields_preview}`
- 错误：`409/40900` 未验签、验签失败、信封损坏、凭据过期；`404/40400`
- 备注：明文**只在内存/带 TTL 的临时对象**中存在，**数据库不落明文**，30min 后自动清理；凭据换取方式定为 `GET /api/v1/finance/preview/{preview_token}`——token 有效期内**可重复读取**（财务常需对照多遍，一次性会干扰工作流），过期即 410；响应不得包含 MinIO 路径

### POST /api/v1/finance/reports/{id}/review
- 权限：`reviewer` ｜ 类型：同步 ｜ 幂等：否（同一单只能决策一次）｜ 实现：S5-5
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| decision | string | 是 | `approve`｜`reject` |
| comment | string | 条件必填 | `reject` 必填，1–500 字符；`approve` 可空 |
| version | int | 是 | 乐观锁 |
| verify_token | string | 条件必填 | `approve` 必填，且必须是未使用过的有效凭据 |

- 出参：**200**，`data` = `{report_id, report_no, status, decision, reviewed_at}`
- 错误：`409/40900` 未验签直接 approve、凭据已使用/过期、重复决策、版本冲突；`422/42200` reject 缺 comment；`403/40301` 非 reviewer
- 备注：`approve` 必须先有有效 verify 凭据（Redis 一次性 token，30min，用后即删）；`reject` 后允许申请人修改重提，旧签名置 `void`；写 `review_logs` 与审计 `action=review`

## 6. 证书与密钥（S4-5 / S4-6）

> 证书由 OpenSSL 3.x 命令行签发（自建 CA，链校验刻意简化）；运行期只做指纹、有效期、吊销、公钥一致性四项校验。
> **本组所有接口均不提供私钥导出**（任何角色、任何形式）；`GET /certs/{id}/private-key` 不存在，且永不实现。`key_records` 只登记 `storage_path`，不是私钥仓库。

### GET /api/v1/certs
- 权限：`admin` 全部；`submitter`/`reviewer` 仅本人证书（`owner_type=user` 且 `owner_id=本人`）｜ 类型：同步（分页）｜ 幂等：是 ｜ 实现：S4-6
- 入参：`owner_type`（`user`｜`server`，仅 admin 可用）、`revoked`（bool）、`page`、`page_size`、`sort_by`（`created_at`｜`not_after`）、`order`
- 出参：**200**，`data = {items: CertOut[], total, page, page_size}`
- 错误：`403/40301` 越权查询他人证书；`422/42200`
- 备注：`owner_type=server` 的财务服务器证书**仅 admin 可见**；不返回私钥或 `storage_path`，**不提供私钥导出**

### POST /api/v1/certs/issue
- 权限：`admin` ｜ 类型：同步 ｜ 幂等：否 ｜ 实现：S4-5 / S4-6
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| owner_type | string | 是 | `user`｜`server` |
| owner_id | int | 条件必填 | `owner_type=user` 时必填 |
| key_type | string | 是 | `sign`（签名）｜`envelope`（信封） |
| initial_passphrase | string | 是 | 8–128 字符，签发时用于 PBKDF2 派生 KEK 并 AES-256-GCM 包裹私钥文件；**不落库、不落日志、不回显**（响应只返回 `key_fp` 与 `storage_path` 的脱敏尾串）。用户首次登录须自助改口令（S4-5 实现） |

- 出参：**201**，`data` = `{cert: CertOut, key_record_id}`（**无任何私钥字段**）
- 错误：`409/40900` 该主体同用途已有有效证书；`422/42200` 主体不存在、用途非法；`500/50020` 密钥生成失败
- 备注：签发时校验"证书公钥 == 私钥推导公钥"，防错配；私钥以 PKCS#8 + 口令派生 KEK 加密后存 `secrets/keys/`；**不提供私钥导出**；写审计 `action=issue_cert`

### POST /api/v1/certs/{id}/revoke
- 权限：`admin` ｜ 类型：同步 ｜ 幂等：是（已吊销返回 200）｜ 实现：S4-6
- 入参（JSON）：`reason`（string，必填，1–200 字符）
- 出参：**200**，`data` = CertOut（`revoked=true`、`revoked_at`）
- 错误：`404/40400`；`422/42200` 缺原因
- 备注：吊销后使用该证书的验签/信封校验一律返回 `cert_invalid`；不提供私钥导出；不做 CRL/OCSP（局限见方案 6.5.6）

## 7. 审计（S4-7）

> 哈希链：`entry_hash = HMAC_SM3(key, prev_hash ‖ ts ‖ actor_id ‖ action ‖ target_id ‖ payload_sha256)`，创世 `prev_hash=0×32`，写入串行加锁。哈希链防"改了不被发现"，不防"数据被读"。

### GET /api/v1/admin/audit
- 权限：**仅 `admin`** ｜ 类型：同步（分页）｜ 幂等：是 ｜ 实现：S4-7
- 入参：`actor_id`、`action`、`target_type`、`target_id`、`trace_id`、`from`/`to`（ISO-8601）、`page`、`page_size`、`sort_by`（`created_at`｜`id`）、`order`
- 出参：**200**，`data = {items: AuditLogOut[], total, page, page_size}`
- 错误：`403/40301` 非 admin；`422/42200` 筛选/分页非法
- 备注：`detail_json` 必须脱敏（身份证/账号打码），**不返回审计 HMAC key**；**不提供 CSV 导出**（D-013：论文不需要，导出会扩大泄露面）；需要留档时由 `verify-chain` + 分页接口按需拉取

### GET /api/v1/admin/audit/verify-chain
- 权限：**仅 `admin`** ｜ 类型：同步（全量扫描，可能较慢）｜ 幂等：是 ｜ 实现：S4-7
- 入参：无
- 出参：**200**，`data` = `{ok: true, checked_count: 1024, broken_at: null, broken_id: null, reason: null}`；断链时 `ok=false`，`broken_at` 为首个断点序号，`reason` 为复算失败原因
- 错误：`403/40301`；`500/50000` 扫描异常
- 备注：按 `id` 升序从创世行复算；不支持指定区间（区间校验无法证明全链完整）；结果建议前端只读展示

## 8. 评测（S6-x）

> 评测为离线跑批（`python -m eval.run`），接口只是触发与查询（**保留**，仅供答辩演示时现场触发）；首版只支持 `invoices_v1` 数据集。

### POST /api/v1/admin/eval/run
- 权限：`admin` ｜ 类型：异步（202）｜ 幂等：否（每次新 run_id）｜ 实现：S6-1 ~ S6-5
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| dataset | string | 是 | 首版仅 `invoices_v1`（白名单校验，其它值 422） |
| config | string | 是 | 配置文件名，如 `configs/base.yaml`；**只接受白名单** |

- 出参：**202**，`data` = `{run_id, status: "pending", created_at}`
- 备注：**评测结果不落库**——`run_id` 是 `backend/eval/results/` 下的结果文件标识（`<config>_<timestamp>`），`GET /admin/eval/runs/{id}` 读文件返回；16 张表里没有 `eval_runs` 表，这是刻意的（见 SCHEMA §0.4 表清单）
- 错误：`403/40301`；`404/40400` 数据集/配置不存在；`422/42200` 不在白名单；`500/50000` 启动失败。**允许并发**（每次独立 `run_id`、结果按时间戳分文件，互不覆盖）
- 备注：`config` 不得是任意路径（防目录穿越）；评测结果落 `eval/results/`，接口只回相对标识、不回绝对路径

### GET /api/v1/admin/eval/runs/{id}
- 权限：`admin` ｜ 类型：同步（轮询）｜ 幂等：是 ｜ 实现：S6-5
- 入参：路径参数 `id`
- 出参：**200**，`data` = `{run_id, status, dataset, config, started_at, finished_at, error, metrics_json: null | object}`（`run_id` 不存在 → `404/40400`；同一 `run_id` 进行中重复查询不报错）
- 错误：`404/40400`；`403/40301`
- 备注：`status ∈ {pending, running, succeeded, failed}`；**不提供报告下载接口**——结果文件直接落在 `backend/eval/results/`，答辩时从仓库目录打开（D-013）

## 9. 系统

### GET /health
- 权限：公开 ｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S0-5
- 入参：无
- 出参：**200**，`data` = `{status: "ok", version: "0.1.0", ts: "2026-11-18T08:30:00Z"}`
- 错误：服务未就绪时 `503`，`data = {status: "degraded", checks: {db, redis, minio}}`（**检查项固定三项**：DB 连通、Redis PING、MinIO bucket 存在；任一失败即 `degraded` 并返回 503，供容器探针与冷启动脚本使用）
- 备注：**两个路径同一实现**——`GET /health`（无前缀，容器探针与运维用）与 `GET /api/v1/health`（业务前缀，前端与文档统一用），响应体完全一致

## 10. 错误码表

> `0` = 成功；错误码沿用方案附录 C1。`40400`/`40900`/`41300`/`42200`/`50000` 为本文件补充的通用码（C1 未列，S0-3 约定）。
> **越权统一 404**：鉴权层识别为"无权限"时，对外返回 `404/40400`，内部审计记 403 事件（方案 3.4 规则 7）。
> **验签/解密失败固定 HTTP 409**（前端统一处理），业务码沿用 C1 的 `50021`/`50030`/`50031` 并用 `reason_code` 分类——此处 HTTP 状态与 code 前三位**不严格对应**，属本项目刻意约定。

| code | HTTP | 含义 | 典型场景 |
|---|---|---|---|
| 40101 | 401 | 未登录 / 令牌过期 / 凭据错误 | 缺 Bearer、token 失效、登录用户名或密码错误 |
| 40301 | 403 | 无权访问该资源 | 内部判定用；对外统一转 404 |
| 40400 | 404 | 资源不存在 | 不存在或越权，二者同响应 |
| 40900 | 409 | 资源冲突（通用） | 用户名已存在、版本冲突、状态不允许、幂等键复用 |
| 40901 | 409 | 重复票据 / 重复影像 | 上传去重命中、同号发票重复报销 |
| 41300 | 413 | 文件过大 | 上传超过配置上限 |
| 42200 | 422 | 参数校验失败 | 字段格式、分页、排序白名单 |
| 42201 | 422 | 字段校验未通过（冲突未决） | 金额合计不符、必填缺失、低置信未确认 |
| 42202 | 422 | 缺少高风险操作的二次确认 | 修改金额类字段未带 `confirm_amount=true`；审批未带有效 `verify_token` |
| 41001 | 410 | 一次性凭据已过期 | `preview_token` 超 30min |
| 50000 | 500 | 服务器内部错误 | 未捕获异常（不泄露堆栈） |
| 50010 | 500 | OCR 引擎异常 | 识别/预处理崩溃 |
| 50020 | 500 | 签名失败 | 私钥不可用、密码运算异常 |
| 50021 | 409 | 验签失败 | `digest_mismatch` / `bad_signature` / `cert_invalid` |
| 50030 | 409 | 信封完整性校验失败 | `mac_mismatch` |
| 50031 | 409 | 解密失败 | `bad_padding` / `bad_key` |

## 11. 数据对象

> 「来源」指 `docs/SCHEMA.md` 的表名（已生成）；本文件与 SCHEMA.md 冲突时以 SCHEMA.md 为准，API 层不得自行发明列名。
> **所有出参对象均不得包含**：私钥、口令、明文 CEK、明文影像、MinIO 对象名/明文路径。

#### InvoiceOut

| 字段 | 类型 | 来源 |
|---|---|---|
| `id` / `uploader_id` / `template_id` / `invoice_type` / `status` / `version` / `created_at` / `updated_at` | int/string | `invoices` |
| `invoice_code` / `invoice_no` / `invoice_date` / `buyer_name` / `buyer_tax_no` / `seller_name` / `seller_tax_no` / `check_code` | string | `invoices` |
| `amount` / `tax_amount` / `total_amount` | decimal(12,2)→string | `invoices` |
| `ocr_status` / `extract_status` / `overall_confidence` / `quality_flag` / `duplicate_of` | string/number | `invoices` |
| `file_id` / `filename` / `sha256` / `mime` / `size` / `page_count` | int/string | `invoice_files` |
| `storage_key` | — | **永不返回** |

#### InvoiceFieldOut

| 字段 | 类型 | 来源 |
|---|---|---|
| `id` / `invoice_id` / `field_name` / `source` / `confirmed` / `updated_by` / `updated_at` | int/string/bool | `invoice_fields` |
| `field_value` / `normalized_value` / `raw_text` / `conflict_value` | string | `invoice_fields` |
| `confidence` | number(4,3) | `invoice_fields` |
| `bbox`（原图坐标 `[x1,y1,x2,y2]`） | array | `invoice_fields.bbox_json` |
| `need_review` / `conflict` | bool | `invoice_fields` |

#### ReportOut

| 字段 | 类型 | 来源 |
|---|---|---|
| `id` / `report_no` / `applicant_id` / `title` / `reason` | int/string | `report_forms` |
| `total_amount` / `item_count` / `status` / `digest_algo` / `digest_hex` / `submitted_at` / `created_at` / `updated_at` | decimal/string | `report_forms` |
| `items[]`（`id` / `invoice_id` / `amount` / `remark`） | array | `report_items` |
| `signature` / `envelope` | object | 见下两节（只含元数据） |

> `report_no` 由服务端按 `RPT-YYYYMM-序号` 生成，`report_forms.report_no` 唯一（SCHEMA §7.1）。`department` 不单独建列，取申请人的 `users.dept`。

#### SignatureOut

| 字段 | 类型 | 来源 |
|---|---|---|
| `id` / `form_id` / `signer_id` / `cert_id` / `verifier_id` | int | `signatures` |
| `algo` / `digest_algo` / `digest_hex` / `signature_b64` / `verify_result` | string | `signatures` |
| `verified` / `verified_at` / `version` / `status`(`active`/`void`) / `created_at` | bool/string | `signatures` |
| 私钥 / 口令 | — | **永不返回** |

#### EnvelopeOut

| 字段 | 类型 | 来源 |
|---|---|---|
| `id` / `form_id` / `file_id` / `recipient_cert_id` | int | `envelopes` |
| `key_algo` / `cipher_algo` / `iv_b64` / `mac_b64` / `payload_sha256` | string | `envelopes` |
| `recipient_cert_fp` / `wrapped_key_len` / `created_at` | string/int | `envelopes`（**派生字段**：`recipient_cert_fp` 由 `recipient_cert_id` join `certificates.fingerprint_sha256` 得到；`wrapped_key_len` = `len(base64d(wrapped_key_b64))`，仅供前端展示与排障，不入库） |
| `wrapped_key_b64` / `ciphertext_b64` / `ciphertext_storage_key` | — | **永不返回**（会话密钥密文与影像密文都不出接口） |

#### CertOut

| 字段 | 类型 | 来源 |
|---|---|---|
| `id` / `owner_type` / `owner_id` / `serial_no` | int/string | `certificates` |
| `subject_dn` / `issuer_dn` / `key_algo` / `public_key_pem` / `cert_pem` | string | `certificates` |
| `not_before` / `not_after` / `revoked` / `revoked_at` / `created_at` | string/bool | `certificates` |
| `fingerprint_sha256` | string | `certificates`（唯一索引） |
| 私钥 / `storage_path` | — | **永不返回** |

#### TemplateOut

| 字段 | 类型 | 来源 |
|---|---|---|
| `id` / `type_code` / `type_name` / `version` / `active` / `created_at` / `updated_at` | int/string/bool | `ticket_templates` |
| `match_keywords` | array | `ticket_templates` |
| `field_specs_json` | object | `ticket_templates`（仅详情返回） |
| `rules[]`（`field_name` / `pattern` / `validator` / `priority` / `active`） | array | `field_rules`（仅详情返回） |

#### AuditLogOut

| 字段 | 类型 | 来源 |
|---|---|---|
| `id` / `actor_id` / `actor_role` / `action` / `target_type` / `target_id` / `trace_id` | int/string | `audit_logs` |
| `detail_json`（**脱敏后**） | object | `audit_logs` |
| `prev_hash` / `entry_hash` / `created_at` | string | `audit_logs` |

> 系统任务（如每日锚定）的 `actor_id` 为 `null`；`actor_role` 取值见 SCHEMA §9.19。

## 12. 定稿记录与剩余待定

> S0-3 已把上一版 14 项待定统一拍板，剩余 3 项属**实现细节**（不影响接口形状），在对应任务里收口即可。

### 12.1 已定稿（D-011 ~ D-018）

| 模块 | 原待定项 | 结论 |
|---|---|---|
| 认证 | `refresh` / `logout` / 吊销策略 | **轮换式刷新 + Redis 白名单登出**（§1 已补两条接口，D-016） |
| 登录 | 并发会话策略 | 允许多端登录，各自独立 `jti`；不做单端互踢 |
| 票据上传 | 单文件 10MB 还是 20MB | **20MB**，配置 `MAX_UPLOAD_MB=20`（D-018） |
| 票据上传 | 是否保留 `POST /invoices/{id}/files`、批量上传接口 | **都不新增**：批量 = 前端串行队列复用单文件接口，进度在前端展示（D-013） |
| 票据 | 弱重复检测是否单独接口 | **S3-7 已覆盖 D-013**：新增只读 `GET /invoices/{id}/duplicate-check`（`{level, matches}`）；上传 `dedup` 与 `duplicate_of` 保留 |
| 模板 | `GET /templates/{code}/rules` 别名、`field_rules` 独立 CRUD | **都不做**：规则随模板 `PUT` 的 `rules` 数组同步维护，`GET /templates/{id}` 已含（D-013） |
| 字段修正 | 金额字段修改权限 | **仅 `reviewer` 且需 `confirm_amount=true`**；`admin` 404（D-017） |
| 并发控制 | 乐观锁缺列、冲突码 | **已加 `version` 列**，冲突一律 **409**（D-011） |
| 报销单 | `report_no` 缺列；`department` | 单号已入 SCHEMA；`department` **不建列**，取 `users.dept` |
| 报销单 | 驳回后编辑与 `rejected → pending_sign` 迁移 | `PATCH /reports/{id}` 仅 `draft` / `rejected` 可改；`POST /reports/{id}/sign` 在 `rejected` 时**先置 `pending_sign` 再签** |
| 报销单 | 凭证包导出与归档检索 | **`GET /reports/{id}/export`**：ZIP 含 `canonical_json`、签名、验签结果、信封元数据、字段快照，**不含明文影像**；归档检索走 `GET /reports/mine?status=archived`（S5-6） |
| 财务 | 私钥口令托管、预览凭据形态 | 口令走**环境变量**（§5 已改）；预览为 `verify_token` → `preview_token`（30min，一次性）（D-016） |
| 证书 | 吊销原因是否落库 | **不建列**：原因写 `audit_logs.detail_json.reason`（服务层必填校验，≤200 字） |
| 审计 | 审计导出 CSV | **不做**（范围外，论文不需要）；列表接口分页 + 筛选已够 |
| 评测 | 在线触发是否保留 | **保留**，仅 `admin`；结果**只落 `backend/eval/results/` 文件**，不建表（D-013） |
| 系统 | `/health` 路径口径 | **两个都提供**：`GET /health`（容器探针）、`GET /api/v1/health`（业务前缀），同一实现 |

### 12.2 剩余待定（实现细节，不阻塞接口）

| 项 | 收口任务 |
|---|---|
| `GET /reports/{id}/export` 的 ZIP 目录结构与文件名规范 | S5-6 |
| `GET /finance/pending` 的默认排序（提交时间倒序 vs 金额倒序） | S5-3 |
| 审计列表的筛选字段白名单（是否支持按 `trace_id` 精确查） | S4-7 |

> 维护规则：接口变化必须先同步本文件；前后端字段命名以本文件为准（`snake_case`）；与本文件冲突的实现视为 bug。文档内不得残留 `TODO 待定`：要么当场拍板并记 `DECISIONS.md`，要么挂进 §12.2 并写明收口任务号。

## 13. 识别产物契约（S3-4）

- **唯一来源**：`backend/app/schemas/ocr.py`；导出的 JSON Schema 见 `docs/schemas/ocr_result.schema.json`
  （生成/校验：`python scripts/export_ocr_schema.py [--check]`）；前端类型见 `web/src/types/ocr.ts`。
- 模型：`OcrLine`（一行文本）、`OcrResult`（一次识别）、`ExtractedField`（一个字段，含归一值与票面原文）、
  `FieldError`（校验/确认错误）、`ExtractionResult`（整票抽取结果）。
- **坐标口径写死**：
  - `OcrLine.box` = 四点 `[[x,y]×4]`，顺时针，**校正图**坐标系；
  - `ExtractedField.bbox` = `[x1,y1,x2,y2]` 外接框，**原图**坐标系（前端画框只认这一种）。
- **字段名白名单**：凡出现 `field_name` / `ExtractedField.name` 的地方，取值只能是
  `invoice_service.INVOICE_FIELD_NAMES`（13 个，含大写金额 `total_amount_upper`）；
  `tests/test_ocr_schema.py` 防漂移。
- **落库口径**：`invoice_fields.field_value` = 票面原文（`raw_value`，如 `￥1,234.56`），
  `normalized_value` = 归一值（如 `1234.56`）；推导值没有单一原文，`field_value` 回退为归一值。
- `GET /invoices/{id}/fields` 的 `items` 即 `invoice_fields` 行（含派生 `review_level`）；
  `ocr_lines` 每次运行**一次 executemany 批量写入**，不逐条 commit。
- 已知限制：本契约只覆盖识别产物；确认/签名域契约见 §4/§6（S5/S4）。
