# S5-3 财务端待审列表 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 8 章 S5-3。
> 契约：`docs/API.md` §5（`GET /finance/pending`；本卡**补** `GET /finance/{id}` 与
> `GET /finance/pending/export` 两条，因为任务卡要求但契约缺）+ §5 的表头约束。
> 目标：审核人能看到全部已提交单据，列表**不实时验签、不返回密文**；能导出 Excel 友好的 CSV。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S5-3 行。

## 1. 范围

本卡做"财务待审"的**只读**部分：列表、详情（元数据）、CSV 导出、前端页面。
**不做**验签/解密/审核决策（S5-4/S5-5）、归档（S5-6）。

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/schemas/finance.py` | 列表/详情/附件出入参 | 新 |
| `backend/app/services/finance_service.py` | 待审查询、详情、CSV 行生成 | 新 |
| `backend/app/api/v1/finance.py` | 三条路由（`/pending`、`/pending/export` 先于 `/{report_id}`） | 新 |
| `backend/app/main.py` | 挂载 finance router | 改 |
| `backend/app/services/report_service.py` | `verify_status_of()` 公开；`envelope_out()` 公开；日期区间改调公共助手 | 改 |
| `backend/app/services/submit_service.py` | **修正**：提交时系统自动验签通过后写 `verified/verify_result/verified_at` | 改 |
| `backend/app/utils/time.py` | `business_day_bounds()`（业务日换算的唯一入口） | 新 |
| `web/src/{types/finance.ts,api/finance.ts,views/FinanceList.vue}` | 财务待审页（筛选/分页/详情抽屉/CSV 下载） | 新 |
| `web/src/router/index.ts`、`utils/format.ts` | `/finance/pending` 换真实页面；验签状态标签 | 改 |
| `backend/tests/test_finance.py` | 集成：列表/筛选/权限/详情/CSV/无密文/2 条 SQL | 新 |

**不动**：迁移（本卡**无 0009**；SCHEMA 不新增列）、0001~0008、密码层参数。

## 2. 关键设计决策

### 2.1 `verify_status` 不新增列：落在 `signatures` 行上

任务卡说"verify_status 在提交时算一次落库"，但 **SCHEMA §5.1 没有这个列**（契约优先），
`signatures` 已有 `verified` / `verify_result` / `verified_at`（§5.3）。因此：

- `verify_status` = 由**该单当前 active 签名**派生：无 active 签名或 `verify_result IS NULL` → `unverified`；
  `ok` → `ok`；其余（`digest_mismatch`/`bad_signature`/`cert_invalid`）→ `failed`；
- 列表 SQL 用 `LEFT JOIN signatures`（active）+ 三态条件，**不在请求里做任何密码运算**；
- **修正 S5-2 的一处契约违背**：SCHEMA §5.3 写明"`submitted` 之后必须已验证"，而 S5-2 提交时验签通过后
  仍写 `verified=false`。本卡把提交里"验签通过"的事实落库：`verified=true`、`verify_result='ok'`、
  `verified_at=now`、`verifier_id=NULL`（= 系统自动验签，SCHEMA §5.3 允许）。于是待审列表默认显示"通过"；
  S5-4 财务端独立验签失败时再把它改成 `failed`（`mac_mismatch`/`bad_padding` 按契约**不写**本列）。

### 2.2 新增两条契约（先补 `API.md`，再写码）

| 接口 | 入参 | 出参 | 语义 |
|---|---|---|---|
| `GET /api/v1/finance/pending/export` | 与 `pending` 相同的筛选（无分页） | `text/csv; charset=utf-8`，**UTF-8 BOM**、`\r\n` | 导出当前筛选下的待审清单 |
| `GET /api/v1/finance/{id}` | 无 | `data = {report: ReportOut, applicant_name, signature: SignatureOut|null, envelope: EnvelopeOut|null, verify_status, attachments: [...]}` | 详情 + 明细 + **附件元数据**（不含明文/密文全文） |

附件项 `attachments[i]`：`file_id`、`invoice_id`、`filename`、`mime`、`size`、`sha256`、`page_count`、
`envelope`（文件级信封的 `EnvelopeOut`，无 `ciphertext_*`/`wrapped_key_*`）。

### 2.3 列表语义与 SQL

- 只列 `status='submitted'`（"待审"）；已 approved/rejected/archived 的不在待审列表（S5-5 后去"我的"/归档看）；
- 复用 `services/repository.filter_visible`（reviewer 可见 submitted 之后），再叠加 `status='submitted'`；
- 查询形状：
  ```
  SELECT report_forms.*, users.username, users.real_name,
         signatures.verified, signatures.verify_result
    FROM report_forms
    JOIN users ON users.id = report_forms.applicant_id
    LEFT JOIN signatures ON signatures.form_id = report_forms.id
                        AND signatures.status = 'active'
   WHERE status='submitted' AND <filters>
   ORDER BY <whitelist> , report_forms.id ASC
  ```
  `paginate(scalar=False)`：count 与数据同一 `Select`，**整页 2 条 SQL**（测试用 SQLAlchemy 事件断言）；
  签名是部分唯一索引（active 至多一条），join 不会放大行数。
- 筛选：`verify_status`（三态，见 §2.1）；`submitted_from/to`（业务日，`Asia/Shanghai` 换算，公共助手）；
  `q`（单号 / 申请人 username / real_name，`escape_like`）；排序白名单 `submitted_at`、`total_amount`，
  非法 → `422/42200`。
- 出参 `applicant_name` 取 `real_name or username`；金额两位小数字符串。

### 2.4 CSV

- 列：`单号,申请人,金额,票据数,提交时间,验签状态`（中文表头，Excel 直接可读）；
- UTF-8 **带 BOM**（`\ufeff`）+ `\r\n` 行尾；`Content-Disposition: attachment; filename="finance-pending-YYYYMMDD.csv"`；
- 用 `StreamingResponse` 逐行 yield（首块 BOM+表头），**不拼接整串**；
- 时间格式 `YYYY-MM-DD HH:MM:SS`（Asia/Shanghai）；
- 与列表用**同一筛选函数**，保证"看到的 = 导出的"；
- 单次导出上限 `10000` 行（超出 `422/42200` 并提示收窄筛选）；财务规模远小于此，但避免拖死进程。

### 2.5 权限与隔离

- 三条接口都挂 `require_roles("reviewer")`：`submitter`/`admin` → `403/40301`（"不得代审"）；
- 详情再走 `get_visible_or_404`：草稿等 reviewer 不可见的单 → `404/40400`；
- 响应**永不**出现 `ciphertext_b64` / `wrapped_key_b64` / `ciphertext_storage_key` / `storage_path`。

### 2.6 前端（`web/src/views/FinanceList.vue`）

- 路由 `/finance/pending`（已有占位，换真实组件，`meta.roles=['reviewer']`）；菜单项"待审核"已存在；
- 筛选（验签状态/提交日期/q/排序）与分页全部以 `route.query` 为唯一状态源（沿用 S2-11 约定）；
- 列：单号、申请人、金额、票据数、提交时间、验签状态（tag 着色：未验灰/通过绿/失败红）、操作"查看"；
- "查看"打开**右侧抽屉**：单据信息 + 签名元数据 + 信封元数据 + 明细（发票号/金额/备注/文件名/大小/SHA-256 前 12 位），
  明确标注"影像预览待 S5-4"；
- "导出 CSV"按钮：axios `responseType=blob`（走鉴权）→ 本地下载；
- 加载中/空/失败三态；提交按钮防重复（导出按钮 loading）。

## 3. 测试（`backend/tests/test_finance.py`，`-m integration`）

复用 `test_submit.py` 的 SM 身份 + FakeStorage + 真链路（建单→明细→签名→提交），构造 5 单。

| # | 用例 | 断言 |
|---|---|---|
| 1 | 5 单可见 | `total=5`；字段齐全（report_no/applicant_name/total_amount/item_count/submitted_at/verify_status） |
| 2 | 默认不实时验签但状态正确 | 提交后 active 签名 `verified=true/verify_result='ok'` → 列表 `verify_status='ok'` |
| 3 | 三态筛选 | 把一单签名改成 NULL 结果 → `unverified` 命中 1；改成 `bad_signature` → `failed` 命中 1 |
| 4 | q 过滤 | 按单号、按申请人真实姓名各命中 |
| 5 | 提交日期过滤 | 今天命中 5；未来日期命中 0；from>to → 422 |
| 6 | 排序 | `total_amount asc/desc` 顺序正确；非法 `sort_by` → 422 |
| 7 | 权限 | submitter 403、admin 403、未登录 401 |
| 8 | 详情 | 200：report + applicant_name + signature + envelope + attachments（每个明细一条，含文件名/大小/sha256/信封元数据）；响应无 `ciphertext_b64`/`wrapped_key_b64`/`ciphertext_storage_key` |
| 9 | 详情隔离 | 草稿单 reviewer → 404；不存在 → 404 |
| 10 | CSV | `text/csv; charset=utf-8`；正文以 `\ufeff` 开头；表头中文；5 行数据；金额/时间格式正确；submitter 403 |
| 11 | CSV 与筛选一致 | `verify_status=ok` 只导 1 行；`q` 生效 |
| 12 | 无 N+1 | 列表请求恰好 **2** 条 SELECT（count + 分页） |

## 4. 验收

1. `pytest tests/test_finance.py -m integration` 全绿；全量单测/集成不回归；`alembic check` 零差异；
2. `npm run typecheck && npm run build` 通过；`/finance/pending` 用 reviewer 登录可见（submitter 403）；
3. 真机/API 冒烟：提交 5 单 → 列表 5 条、CSV 5 行且 Excel 中文正常（用 BOM 与 CRLF 保证）；
4. 文档：API.md（两条新接口 + S5-3 备注）、PROGRESS、DECISIONS **D-086**、HANDOFF（下一步 S5-4）、AGENTS 锚点。

## 5. 已知限制

- `verify_status` 由签名行派生，**没有独立列**；若将来要"每单多次验签历史"，需在 SCHEMA 加表/列（本期不做）；
- 待审列表固定 `status='submitted'`，不含已审/驳回/归档（S5-5/S5-6 再给对应视图）；
- CSV 是"全量筛选结果"而非游标流式（服务端先取到内存再逐行 yield），上限 10000 行；
- 详情只有元数据（影像与字段明文要等 S5-4 的 verify + decrypt 临时凭据）；
- 前端未做端到端点击自动化（typecheck/build + 手动走查路径）。