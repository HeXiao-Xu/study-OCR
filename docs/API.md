# API.md — 接口契约

> **来源**：《OCR票据识别与加密系统-毕业设计完整方案》第 3 章（3.2 功能清单 A–G、3.4 关键业务规则）、第 6 章（6.3 抽取输出、6.4 人工确认、6.5 密码模块、6.6 状态机）、附录 B（关键接口草案）、附录 C1（统一响应与错误码）；字段名以 `docs/SCHEMA.md` 为准（已生成，冲突时以 SCHEMA.md 为唯一依据）。
> **本文件是前后端的共同语言**：接口变更必须先改这里、再写代码；与本文件冲突的实现视为 bug。
> **状态**：当前为 **S0-3 草案**。标 `TODO 待定` 的部分由对应任务号定稿；文中任务号（S2-2 / S3-x / S4-x / S5-x / S6-x）出自《AI协作开发实施计划》。

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

> 三条接口均套统一壳。注册仅产生 `submitter`；`reviewer` 由 `admin` 在后台指定，`admin` 由初始化脚本创建，**均不支持自助注册**。
> `POST /auth/refresh`、`POST /auth/logout` 暂不列入（见 §12，S2-2 定稿）。

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
- 权限：公开 ｜ 类型：同步 ｜ 幂等：是（无副作用；并发会话策略 TODO 待定）｜ 实现：S2-2
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| username | string | 是 | 3–64 字符，trim + 小写后匹配 |
| password | string | 是 | 8–128 字符 |

- 出参：**200**，`data` = `{access_token, token_type: "bearer", expires_in: 1800, user: UserOut}`
- 错误：`401/40101` 用户名或密码错误（账号不存在与密码错误**同码同文案**，不泄露账号是否存在）
- 备注：`refresh_token` 是否下发见 §12（S2-2 定稿）；登录失败计入审计 `action=login_failed`

### GET /api/v1/auth/me
- 权限：登录用户（三角色）｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S2-2
- 入参：无
- 出参：**200**，`data` = UserOut（同上）
- 错误：`401/40101` 未提供凭据 / 令牌过期 / 用户已禁用；`404/40400` 用户不存在
- 备注：按 `sub` 回表取用户，**不信 token 内的 `role`**

## 2. 票据上传与识别（S2-5 / S2-6 / S3-x）

> 生命周期见方案 6.6，落库拆成两列，**不要混用**：票据业务状态 `status` 只有 `draft → confirmed → in_report → archived`（`void` 为软删终态）；识别状态 `ocr_status` 为 `pending → running → succeeded|failed`；"待确认"落在 `extract_status=need_confirm`，不是 `status` 值。
> 上传链路顺序**定死**：`sha256 查重 → 内存中信封加密 → 写 MinIO → 落库 → 投递异步任务`。

### POST /api/v1/invoices/upload
- 权限：`submitter`（本人）｜ 类型：上传 + 异步 ｜ 幂等：否（按 `sha256` 去重）｜ 实现：S2-6 上传 / S3-1 识别
- 入参（`multipart/form-data`）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| file | file | 是 | `image/jpeg`·`image/png`·`application/pdf`，≤20MB（方案 3.2 A1；实施计划写 10MB，**以配置为准，S2-6 定稿**）；MIME 白名单 + 魔数双校验；PDF ≤5 页 |

- 出参：**202**，`data`：

```json
{"invoice_id": 101, "file_id": 88, "job_id": 501, "sha256": "9f2c...",
 "status": "draft", "ocr_status": "pending",
 "dedup": {"hit": false, "invoice_id": null}}
```

- 错误：`409/40901` 重复影像（`data.invoice_id` 为既有票据，客户端直接跳详情）；`413/41300` 超大小上限；`422/42200` 类型/魔数不符、PDF 页数超限、空文件；`500/50000` 存储或队列不可用
- 备注：**响应不得出现 MinIO 对象名或明文路径**；明文只在内存中存在一次，信封加密后才落盘（收方为财务服务器证书）；入库失败必须补偿删除已写对象；重复命中不再写对象存储、不再投任务

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
- 备注：详情不返回影像二进制；预览走 `GET /invoices/{id}/image`

### GET /api/v1/invoices/{id}/ocr-status
- 权限：本人，或 `reviewer` ｜ 类型：同步（轮询）｜ 幂等：是 ｜ 实现：S3-1 / S2-7
- 入参：无
- 出参：**200**，`data` = `{invoice_id, ocr_status, progress, stages: [{stage, status, elapsed_ms, engine, engine_version, retry_count, error}], started_at, finished_at}`

```json
{"invoice_id": 101, "ocr_status": "running", "progress": 60,
 "stages": [{"stage": "preprocess", "status": "succeeded", "elapsed_ms": 420,
             "engine": "paddleocr", "engine_version": "2.7.3", "retry_count": 0, "error": null}],
 "started_at": "2026-11-18T08:30:01Z", "finished_at": null}
```

- 错误：`404/40400` 不存在或无权访问
- 备注：`stage ∈ {preprocess, detect, recognize, extract}`；`progress` 为 0–100 的粗粒度值（**TODO 待定**：S3-1 是否按阶段加权）；失败重试上限 3 次；建议轮询间隔 1–2s、总时长 ≤5min

### GET /api/v1/invoices/{id}/fields
- 权限：本人，或 `reviewer` ｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S3-2 / S3-4
- 入参：无
- 出参：**200**，`data` = `{invoice_id, overall_confidence, needs_confirm: [field_name], conflicts: [{field_name, reason, expected, actual}], items: InvoiceFieldOut[]}`

```json
{"invoice_id": 101, "overall_confidence": 0.89, "needs_confirm": ["buyer_tax_no"],
 "conflicts": [{"field_name": "total_amount", "reason": "sum_mismatch",
                "expected": "1234.56", "actual": "1200.00"}],
 "items": [{"field_name": "total_amount", "field_value": "¥1,234.56",
            "normalized_value": "1234.56", "confidence": 0.98,
            "bbox": [700, 610, 900, 640], "source": "rule",
            "need_review": false, "conflict": true}]}
```

- 错误：`404/40400`
- 备注：`items` 含 12 个核心字段；低置信（<0.85）字段 `need_review=true`；冲突**不静默修正**，`conflict=true` 且保留 `conflict_value`

### PATCH /api/v1/invoices/{id}/fields
- 权限：本人（`submitter`，仅非金额字段）；`reviewer`（全部字段，含金额）；`admin` → 404 ｜ 类型：同步 ｜ 幂等：否 ｜ 实现：S3-6
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| version | int | 是 | 乐观锁版本（`invoices.version`）；与库内不一致 → `409/40900` |
| fields | array | 是 | 1–12 项；`field_name` 必须在票据字段集内 |
| fields[].field_name | string | 是 | 如 `total_amount`、`buyer_tax_no` |
| fields[].value | string | 是 | 文本值；金额按两位小数存储 |
| fields[].confirm | bool | 否 | 人工确认标记，默认 `false` |

- 出参：**200**，`data` = `{invoice_id, version, status, updated: [{field_name, before, after}], needs_confirm, conflicts}`
- 错误：`404/40400`；`409/40900` 版本冲突 / 状态不允许（仅 `status=draft` 且 `extract_status ∈ {raw, need_confirm}` 可改）；`422/42200` 字段名非法、值格式非法；`422/42201` 修改后仍存在未决冲突
- 备注：每次修改写 `audit_logs`（`action=confirm_field`，`detail_json` 含 before/after，**已脱敏**）；`source` 由服务端置 `manual`；金额字段修改要求 `reviewer`（**权限细化 TODO 待定**，S3-6 写入 DECISIONS）；已 `confirmed`/`in_report` 的票据拒绝修改

### POST /api/v1/invoices/{id}/reocr
- 权限：本人 ｜ 类型：异步（202）｜ 幂等：否 ｜ 实现：S3-3
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| fields | array | 是 | 1–6 个字段名；必须是低置信且已有 bbox 的字段 |
| version | int | 否 | 传入则校验乐观锁 |

- 出参：**202**，`data` = `{invoice_id, job_id, fields}`
- 错误：`404/40400`；`409/40900` 状态不允许（已确认/已归档）；`422/42200` 字段无 bbox、超过 6 个、或该字段已重试过
- 备注：做法为 bbox 外扩 8px → 放大 2 倍 → 单字段重识别，取更高分，`source=ocr_retry`；单张票据总增量耗时 ≤1.5s，超出放弃并转人工确认

### POST /api/v1/invoices/{id}/confirm
- 权限：本人 ｜ 类型：同步 ｜ 幂等：是（已 `confirmed` 重复调用返回当前状态）｜ 实现：S3-6
- 入参：无（可选 `version`，见备注）

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| version | int | 否 | 传入则做乐观锁校验 |

- 出参：**200**，`data` = InvoiceOut（`status=confirmed`、`extract_status=confirmed`）
- 错误：`404/40400`；`409/40900` 状态不允许；`422/42201` 整单校验未通过，`data.fields = [{field_name, error}]` 逐字段列出
- 备注：**整单确认闸门**——服务端重跑 validators（必填齐备 + 无未决冲突 + 低置信项均已确认），**不信前端传来的"已校验"标记**；通过后写审计

### DELETE /api/v1/invoices/{id}
- 权限：本人 ｜ 类型：同步（软删）｜ 幂等：是（已 `void` 返回 204）｜ 实现：S2-5
- 入参：无
- 出参：**204** 无响应体（`status=void`，行保留）
- 错误：`404/40400`；`409/40900` 已加入报销单 / 已提交 / 已归档，禁止删除
- 备注：不物理删除记录与 MinIO 对象（审计与追溯需要）；`(invoice_code, invoice_no) WHERE status <> 'void'` 唯一索引允许同号重传

### GET /api/v1/invoices/{id}/image
- 权限：本人，或 `reviewer`；`admin` → 404 ｜ 类型：下载（二进制流）｜ 幂等：是 ｜ 实现：S3-5 / S5-4
- 入参：`page`（int，PDF 页码，默认 1）；`variant`（`original`｜`preprocessed`，默认 `original`）
- 出参：**200**，图片/PDF 页字节；响应头 `Content-Type`、`ETag=<sha256>`、`Cache-Control: private, no-store`、`X-Content-Type-Options: nosniff`
- 错误：`404/40400`；`409/40900` 信封缺失或已损坏；`409/50030` MAC 校验失败；`409/50031` 解密失败（均**不返回任何明文片段**）
- 备注：服务端在内存中解信封，响应结束立即释放；**绝不返回 `storage_key`、MinIO 路径或公开签名 URL**

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
- 备注：版本只增不改；新版本须复制或重建对应 `field_rules`（**TODO 待定**：S2-8 定稿复制策略）；更新后 60s 内生效；同 `type_code` 至多一版 `active=true`；写审计保留旧版本

### POST /api/v1/templates/{id}/test
- 权限：**仅 `admin`**（`reviewer` 只读，不允许试跑）｜ 类型：同步（上传 + 即时计算）｜ 幂等：是（不落库）｜ 实现：S2-8
- 入参（`multipart/form-data`）：`file`（必填，JPG/PNG，≤20MB）
- 出参：**200**，`data` = `{template_id, type_code, matched_keywords, overall_confidence, elapsed_ms, fields: [{field_name, value, confidence, bbox, source}]}`
- 错误：`404/40400`；`422/42200` 图片非法 / 模板未启用；`500/50010` OCR 引擎异常
- 备注：**不建票据、不投任务、不落 MinIO**，图片只进内存；仅用于调模板；不返回任何存储路径

## 4. 报销单（S5-1 / S5-2）

> 单号 `RPT-YYYYMM-序号` 由 PostgreSQL 序列生成；金额一律 `NUMERIC(12,2)`；状态 `draft → pending_sign → signed → encrypted → submitted → approved|rejected → archived`。

### POST /api/v1/reports
- 权限：`submitter` ｜ 类型：同步 ｜ 幂等：否 ｜ 实现：S5-1
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| title | string | 是 | 1–100 字符 |
| reason | string | 是 | 1–500 字符 |

- 出参：**201**，`data` = ReportOut（`status=draft`、`total_amount="0.00"`、`items=[]`）
- 错误：`422/42200` 参数校验失败；`401/40101` 未登录
- 备注：`total_amount` 不接受客户端传值，由明细汇总；建单写审计 `action=report_create`

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
- 备注：合计由服务端汇总 `items.amount`，**不信客户端**；同一单内重复添加返回 `409/40901`（`UNIQUE(form_id, invoice_id)`）；跨单引用同一票据由业务层拦截，**SCHEMA 尚无跨单唯一约束**，是否补约束 TODO（S2-1 / S5-1）；添加成功后票据 `status` 置 `in_report`（同一事务，SCHEMA 5.2 跨表不变式）

### POST /api/v1/reports/{id}/sign
- 权限：本人 `submitter` ｜ 类型：同步 ｜ 幂等：否（重复签名为重签：旧签名置 `void`、`signatures.version` +1）｜ 实现：S5-2
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| private_key_passphrase | string | 是 | 解锁本人签名私钥；只在内存停留，**不落日志、不回显**。**TODO 待定**：S4-5 是否改为服务端托管口令 |

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
- 备注：**不返回 `ciphertext_b64`、`wrapped_key_b64`、`ciphertext_storage_key`**；明文影像只经 `decrypt` 的临时预览

### GET /api/v1/reports/mine
- 权限：`submitter`（仅本人）；`reviewer`/`admin` → 403 ｜ 类型：同步（分页）｜ 幂等：是 ｜ 实现：S5-1
- 入参：`status`、`date_from`/`date_to`、`page`、`page_size`、`sort_by`（`created_at`｜`submitted_at`）、`order`
- 出参：**200**，`data = {items: ReportOut[]（不含 items 明细）, total, page, page_size}`
- 错误：`403/40301`；`422/42200` 排序/日期非法
- 备注：列表只给汇总，明细走详情接口，避免 N+1

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
| private_key_passphrase | string | 是 | 财务服务器信封私钥口令；只在内存停留，不落日志、不回显。**TODO 待定**：S5-4 是否改服务端托管 |

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
- 备注：明文**只在内存/带 TTL 的临时对象**中存在，**数据库不落明文**，30min 后自动清理；预览 URL 形态与换取方式 **TODO 待定**（S5-4）；响应不得包含 MinIO 路径

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
| private_key_passphrase | string | 是 | 8–128 字符，仅用于加密私钥；**不落库、不落日志、不回显**；**TODO 待定**（S4-5 是否服务端托管） |

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
- 备注：`detail_json` 必须脱敏（身份证/账号打码），**不返回审计 HMAC key**；导出 CSV **TODO 待定**（S4-7）

### GET /api/v1/admin/audit/verify-chain
- 权限：**仅 `admin`** ｜ 类型：同步（全量扫描，可能较慢）｜ 幂等：是 ｜ 实现：S4-7
- 入参：无
- 出参：**200**，`data` = `{ok: true, checked_count: 1024, broken_at: null, broken_id: null, reason: null}`；断链时 `ok=false`，`broken_at` 为首个断点序号，`reason` 为复算失败原因
- 错误：`403/40301`；`500/50000` 扫描异常
- 备注：按 `id` 升序从创世行复算；不支持指定区间（区间校验无法证明全链完整）；结果建议前端只读展示

## 8. 评测（S6-x）

> 评测为离线跑批（`python -m eval.run`），接口只是触发与查询；首版可只支持 `invoices_v1` 数据集。**TODO 待定**：S6-5 定稿是否保留在线触发（答辩演示用）。

### POST /api/v1/admin/eval/run
- 权限：`admin` ｜ 类型：异步（202）｜ 幂等：否（每次新 run_id）｜ 实现：S6-1 ~ S6-5
- 入参（JSON）：

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| dataset | string | 是 | 首版仅 `invoices_v1`（TODO 待定） |
| config | string | 是 | 配置文件名，如 `configs/base.yaml`；**只接受白名单** |

- 出参：**202**，`data` = `{run_id, status: "pending", created_at}`
- 备注：**评测结果不落库**——`run_id` 是 `backend/eval/results/` 下的结果文件标识（`<config>_<timestamp>`），`GET /admin/eval/runs/{id}` 读文件返回；16 张表里没有 `eval_runs` 表，这是刻意的（见 SCHEMA §0.4 表清单）
- 错误：`403/40301`；`422/42200` 数据集/配置不在白名单；`409/40900` 已有运行中的评测（**TODO 待定**是否允许并发）；`500/50000` 启动失败
- 备注：`config` 不得是任意路径（防目录穿越）；评测结果落 `eval/results/`，接口只回相对标识、不回绝对路径

### GET /api/v1/admin/eval/runs/{id}
- 权限：`admin` ｜ 类型：同步（轮询）｜ 幂等：是 ｜ 实现：S6-5
- 入参：路径参数 `id`
- 出参：**200**，`data` = `{run_id, status, dataset, config, started_at, finished_at, error, metrics_json: null | object}`（`run_id` 不存在 → `404/40400`；同一 `run_id` 进行中重复查询不报错）
- 错误：`404/40400`；`403/40301`
- 备注：`status ∈ {pending, running, succeeded, failed}`；**TODO 待定**：是否提供报告下载（S6-5）

## 9. 系统

### GET /health
- 权限：公开 ｜ 类型：同步 ｜ 幂等：是 ｜ 实现：S0-5
- 入参：无
- 出参：**200**，`data` = `{status: "ok", version: "0.1.0", ts: "2026-11-18T08:30:00Z"}`
- 错误：服务未就绪时 `503`，`data = {status: "degraded", checks: {...}}`（**TODO 待定**：检查项是否含 DB/Redis/MinIO，S0-5 定稿）
- 备注：**无 `/api/v1` 前缀**；`/api/v1/health` 兼容别名 **TODO 待定**（S0-5 与实施计划口径不一致，需拍板）

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

## 12. 待定项清单

| 模块 | 缺口 / TODO | 定稿任务 |
|---|---|---|
| 认证 | `refresh` / `logout` / token 吊销策略、`refresh_token` 是否下发 | S2-2 |
| 票据上传 | 单文件上限 10MB 还是 20MB；是否保留 `POST /invoices/{id}/files` 旧草案路径；批量上传 | S2-6 / S3-8 |
| 票据 | 弱重复检测接口（`GET /invoices/{id}/duplicate-check`）、批量上传进度 | S3-7 / S3-8 |
| 模板 | 附录 B 草案的 `GET /templates/{code}/rules` 是否保留别名（当前由详情接口覆盖）；`field_rules` 独立 CRUD 未列（S2-8 任务卡要求） | S2-8 |
| 字段修正 | 金额字段修改的权限细化（是否允许 admin 兜底、是否需要二次确认） | S3-6 |
| 并发控制 | ~~乐观锁字段缺列~~ **已定**：`invoices.version` / `report_forms.version` 已入 SCHEMA（`INTEGER NOT NULL DEFAULT 0`，应用层自增）；待定项降为"冲突响应用 409 还是 412"→ 取 **409**，无需再议 | S2-1 落地 |
| 报销单 | ~~`report_no` 缺列~~ **已定**：`report_forms.report_no TEXT NOT NULL UNIQUE`，格式 `RPT-YYYYMM-序号`（SCHEMA 待复核第 20 项）；`department` **不建列**，需要时取 `users.dept`（申请人部门），避免同一事实两处存 | S5-1 |
| 报销单 | 驳回后编辑 `title`/`reason` 的接口、`rejected → pending_sign` 的显式迁移未列（当前靠重加明细/重签隐式推进） | S5-5 |
| 报销单 | 凭证包导出（ZIP/PDF）与归档检索、归档后只读校验 | S5-6 |
| 财务 | 服务端私钥口令托管方式（请求体 vs 环境变量）、预览 URL 形态与一次性凭据 | S5-4 |
| 证书 | 密钥生成口令来源、吊销原因是否必填/落库、证书吊销列表（不做 CRL/OCSP 的边界说明） | S4-5 / S4-6 |
| 审计 | 审计导出 CSV、锚定记录（每日最后 `entry_hash` 外存） | S4-7 |
| 评测 | 在线触发是否保留、实验子集选择、运行中并发策略、报告下载与轮询 | S6-5 |
| 系统 | ~~`/health` 路径口径~~ **已定**：两个都提供——`GET /health`（无前缀，给容器健康检查与运维探针）、`GET /api/v1/health`（业务前缀，给前端与文档统一）；两者同一实现，响应体一致 | S0-5 |

---

> 维护规则：接口变化必须先同步本文件；前后端字段命名以本文件为准（`snake_case`）；与本文件冲突的实现视为 bug。文档内 `TODO 待定` 必须在对应任务验收前清零。
