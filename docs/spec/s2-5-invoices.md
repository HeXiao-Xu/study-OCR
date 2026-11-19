# S2-5 票据表与 CRUD（invoices / invoice_files）— 设计（spec）

> 任务卡：S2-5 ｜ 契约：`docs/API.md` §2 / §11（InvoiceOut）｜ 上游：S2-1 模型、S2-3 隔离、S2-4 分页 ｜ 下游：S2-6 上传、S3 识别、S5 报销
> 状态：**已实现（2026-11-19）**；§4 的冲突按方案 A（契约优先）落地，实测见 §6。

## 1. 目标与范围

把票据的**读、软删、字段修改、状态机**做成正式服务与接口；上传（建票）仍归 S2-6。

**做**（方案 A，推荐）：
- `services/invoice_service.py`：
  - `create_draft(...)`：**内部工厂**（S2-6 上传成功后调用；测试/脚本也可用），不暴露 HTTP；
  - `transition(invoice, target, actor, expected_version=None)`：**唯一**状态机入口；
  - `list_invoices(...)` / `get_invoice_detail(...)` / `soft_delete(...)` / `update_fields(...)`；
- `api/v1/invoices.py`（只注册契约里存在的 4 条）：
  - `GET /invoices`（筛选 + 分页 + 行级隔离）
  - `GET /invoices/{id}`（invoice + fields + latest_job）
  - `DELETE /invoices/{id}`（软删 = `status='void'`，幂等）
  - `PATCH /invoices/{id}/fields`（契约版批量改字段；S3-6 复用）
- `schemas/invoice.py`：`InvoiceOut` / `InvoiceFieldOut` / `InvoiceDetailData` / `LatestJobOut` /
  `InvoiceFieldsUpdateRequest` / `InvoiceFieldsUpdateData` / `InvoiceListQuery`
- `exceptions.py`：补 `42201`（未决冲突）与 `42202`（金额修改缺二次确认）
- `tests/test_invoices.py`：服务层 + HTTP 全链路（临时库）

**不做**：
- `POST /invoices` 与 `PATCH /invoices/{id}`（契约不存在；建票走 S2-6 的 `/invoices/upload`）；
- `POST /invoices/{id}/confirm`、`/reocr`、`GET /{id}/ocr-status`、`GET /{id}/fields`（S3 任务）；
- 审计落库（`confirm_field` 等动作待 S4-7 哈希链；沿用 D-040 原则）；
- 影像下载 / 信封拆封（S3-5 / S5-4）。

## 2. 契约口径（以 API.md 为准）

### 2.1 状态机（D-011 定稿，**不采用任务卡那张图**）

`invoices.status` 只有 5 值：`draft / confirmed / in_report / archived / void`。
任务卡里的 `ocr_pending / ocr_done / signed / submitted / approved / rejected` 分别属于
`ocr_status` 列与 `report_forms.status`，**不得混进 `invoices.status`**。

允许迁移（`transition()` 唯一实现，非法迁移 `409/40900` 并说明当前状态）：

| from | to | 触发 |
|---|---|---|
| `draft` | `confirmed` | 整单确认（S3-6） |
| `draft` / `confirmed` | `void` | 软删（本卡 DELETE） |
| `confirmed` | `in_report` | 加入报销单（S5） |
| `in_report` | `archived` | 归档（S5） |
| `void` | — | 终态 |

`ocr_status` 的 `pending→running→succeeded|failed` 由 Worker 写（S3-1），不进本状态机。

### 2.2 接口

| 接口 | 行为 |
|---|---|
| `GET /invoices` | submitter 仅本人 / reviewer 全量 / admin **404**；筛选 `status`、`extract_status`、`invoice_type`、`q`（发票号或销售方名称子串，转义 `%_`）、`date_from/date_to`（按 `invoice_date` 闭区间）；默认**不含 `void`**；排序白名单 `created_at/invoice_date/total_amount`，默认 `created_at desc`；`PageResult` 出参 |
| `GET /invoices/{id}` | `{invoice, fields, latest_job}`；他人 / admin / 不存在一律 **404**；`latest_job` 取 `ocr_jobs` 最新一条（无则 `null`） |
| `DELETE /invoices/{id}` | 仅本人；`draft/confirmed` → `void`（**204 无响应体**）；已 `void` 重复调用仍 204；`in_report/archived` → **409**；他人 / reviewer / admin → 404 |
| `PATCH /invoices/{id}/fields` | 见 §2.3；契约标注 S3-6，本卡提前实现其行为（S3-6 只做复核与审计） |

### 2.3 字段修改（契约 §PATCH /invoices/{id}/fields）

- 入参：`version`（必填，乐观锁）、`fields[]`（1–12 项，`field_name` 必须在 12 字段集内）、
  `confirm_amount`（修改金额类字段时必填 true）。
- 权限（D-017）：`submitter` 只能改非金额字段（改金额 → **403**）；`reviewer` 可改全部字段，
  但金额类必须 `confirm_amount=true`（否则 **422/42202**）；`admin` → 404。
- 状态：仅 `status=draft` 且 `extract_status ∈ {raw, need_confirm}` 可改，否则 **409**。
- 版本不一致 → **409/40900**；字段名非法 / 值格式非法 → **422/42200**；
  更新后仍有未决冲突 → **422/42201**（事务回滚，不落半套修改）。
- 成功：字段 upsert 到 `invoice_fields`（`source='manual'`、`confidence=1.000`、`confirmed` 按入参、
  `need_review=not confirm`），同步 `invoices` 的对应列，`version+1`，返回
  `{invoice_id, version, status, updated:[{field_name,before,after}], needs_confirm, conflicts}`。
- **12 字段集**：`invoice_code / invoice_no / invoice_date / buyer_name / buyer_tax_no /
  seller_name / seller_tax_no / amount / tax_amount / total_amount / check_code / passenger_name`
  （`passenger_name` 只存 `invoice_fields`，`invoices` 表没有该列——SCHEMA 原文如此）。

## 3. 设计

```
api/v1/invoices.py        路由：参数校验 → service → ok()/204
services/invoice_service.py
  INVOICE_FIELD_NAMES / AMOUNT_FIELDS
  create_draft(db, uploader_id, file_id, **fields) -> Invoice
  transition(db, invoice, target, actor, expected_version=None) -> Invoice
  list_invoices(db, user, query) -> PageResult
  get_invoice_detail(db, user, invoice_id) -> (Invoice, fields, latest_job)
  soft_delete(db, user, invoice_id) -> None
  update_fields(db, user, invoice_id, payload) -> UpdateResult
schemas/invoice.py        出入参（金额一律两位小数字符串）
```

要点：
- **列表/详情复用 S2-3/S2-4**：`filter_visible` + `paginate`；列表用显式 join 取 `invoice_files`
  的文件元数据（一次查询，不做逐行回查），**永不返回 `storage_key`**。
- **admin 列表 404**：仓库层给 admin `false()`（空列表），但契约要求 404，
  路由先做角色闸门（submitter/reviewer 之外 → `NotFoundError`）。
- **软删与唯一索引**：`(invoice_code, invoice_no) WHERE status <> 'void'` 在 `void` 后释放，
  同号重传再由 S2-6 的正常创建路径处理；本卡用集成测试证明"删除后同号可再建"。
- **字段校验**：金额用 `Decimal` + `parse_money`；日期 `parse_date`；代码/号码按位数；
  税号允许 15/18/20 位（18 位才跑 USCC 校验），避免把合法的 15/20 位税号误判——
  S1-5 的 `validate_uscc` 只认 18 位，这里不能直接拿它做硬失败。
- **冲突**：只对金额三件套、日期、代码/号码、18 位税号做交叉校验；
  有 error 级问题 → 字段 `conflict=true`、`conflict_value=说明`；更新后仍有问题 → 42201 回滚。
- **事务**：`update_fields`/`soft_delete`/`transition` 各自一个事务，失败回滚（AGENTS §10.2）。

## 4. 与任务卡的冲突（报告，按契约执行）

1. **建草稿没有 HTTP 入口**：任务卡要求 `POST /invoices`，但契约里建票是
   `POST /invoices/upload`（S2-6，含 sha256 去重/信封/MinIO）。S2-5 提供内部 `create_draft()`，
   S2-6 上传成功后调用；**不新增契约外接口**。
2. **`PATCH /invoices/{id}` 不存在**：契约只有 `PATCH /invoices/{id}/fields`（S3-6）。
   本卡实现契约版的字段修改，S3-6 做复核/审计；不新增 `PATCH /invoices/{id}`。
3. **状态机值**：任务卡那张图与 SCHEMA/API.md 冲突，以 D-011 为准（见 §2.1）。
4. **验收"建单→改字段→查详情 curl 链路"**：建单在 S2-6 前只能走内部服务；
   本卡用 TestClient 走等价链路（`create_draft` → `PATCH fields` → `GET detail`），
   S2-6 落地后再补真正的 curl 建单链路。

**已拍板（2026-11-19，方案 A）**：按契约实现——不新增 `POST /invoices` / `PATCH /invoices/{id}`；
字段修改用 `PATCH /invoices/{id}/fields`；状态机按 D-011。最终实现与测试见 `tests/test_invoices.py`。

## 5. 测试设计（`tests/test_invoices.py`，临时库 `study_ocr_inv_`）

- 服务层：`create_draft` 建草稿（owner/file 绑定、version=0）；`transition` 合法/非法（409）/
  版本冲突；`soft_delete` 状态限制与幂等；`update_fields` 权限/版本/状态/校验/冲突回滚。
- HTTP：列表（三权限 + 筛选 + 分页 + 默认不含 void + q 转义 + 排序）、详情（fields/latest_job/404）、
  软删（204/幂等/409/404）、字段修改（200 链路、403、42202、409、42200、42201）。
- 唯一索引：建 → 软删 → 同 `(code,no)` 再建成功。
- 金额序列化：响应里 `"1234.56"` 字符串，不是浮点。

## 6. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest -m integration tests/test_invoices.py -p no:warnings
../.venv/Scripts/python.exe -m pytest -p no:warnings
../.venv/Scripts/python.exe -m pytest -m integration -p no:warnings
```

**实测（2026-11-19）**：`tests/test_invoices.py` **31 passed**；`pytest 310 passed`、
`pytest -m integration 118 passed`；`alembic check` 零差异（无库结构变更）。

## 7. 风险与回滚

| 风险 | 处理 |
|---|---|
| 字段修改与 S3-6 重叠 | 本卡按契约实现行为，S3-6 只加复核/审计；若 S3-6 需要改行为，先改 API.md |
| 42201 过严（已有冲突的票据不能改无关字段） | 按契约字面执行；S3-6 若需放宽，先改契约 |
| 软删后同号重传撞 `uq_invoices_code_no` | `void` 已释放；测试覆盖；S2-6 另按 `sha256` 去重 |
| 列表遗漏 `storage_key` 泄露 | InvoiceOut 不定义该字段；测试断言响应无此 key |

## 8. 收口

- `PROGRESS` 勾 S2-5 + 实测；`DECISIONS`：D-043（状态机与任务卡冲突，按 D-011；
  admin 列表 404 的实现位置；字段修改提前到 S2-5 的边界）；
- `AGENTS` §10.8 回填"票据 CRUD 与状态机"锚点；
- `HANDOFF` 下一步 S2-6；提交 `feat(s2-5): ...`；**push 由用户执行**。