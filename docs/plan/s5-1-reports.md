# S5-1 报销单创建与明细 — 实现计划（plan）

> 对应 spec：[`docs/spec/s5-1-reports.md`](../spec/s5-1-reports.md)｜
> 任务卡：实施计划第 8 章 S5-1｜状态：**已实现（2026-11-21）**，Task 0–7 全部执行并验证
> 只动 `backend/app/{schemas/report.py,services/report_service.py,api/v1/reports.py,models/enums.py,
> services/pagination.py}`、迁移 0008、`backend/tests/test_reports.py` 与文档；
> **不动 0001~0007 迁移、不动 report_forms/report_items 列、不动密码层**。

## Global Constraints

- 金额：`Decimal` + `NUMERIC(12,2)`，JSON 出参两位小数字符串；**禁止 float**；
- 角色：submitter 本人；reviewer 只读已提交；admin 一律 404（职责分离）；
- 行级隔离只走 `services/repository.py`，路由不写 where；
- 状态机只有一个入口 `transition()`；一个用例一个事务，失败整体回滚；
- 审计与业务同事务（`append_audit` 不 commit，D-081）；`detail_json` 只放单号与状态；
- 越权对外 404；角色级拒绝 403；不泄露资源存在性、不返回任何密钥/密文。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/schemas/report.py` | 四种出参 + 两种入参 + 详情壳 + 列表 query | 新 |
| `backend/app/services/report_service.py` | `ALLOWED_TRANSITIONS` / `transition` / `create_report` / `add_item` / `get_report_detail` / `list_my_reports` | 新 |
| `backend/app/api/v1/reports.py` | 四条路由（`/mine` 先于 `/{report_id}` 注册） | 新 |
| `backend/app/api/v1/../main.py` | 挂载 reports router | 改 |
| `backend/app/models/enums.py` | `AuditAction.REPORT_CREATE` | 改 |
| `backend/alembic/versions/0008_report_number_and_audit_action.py` | `report_no_seq` + 放开 `ck_audit_logs_action` | 新 |
| `backend/app/services/pagination.py` | `apply_sort(..., nulls_last=False)` / `paginate` 透传 | 改 |
| `backend/tests/test_reports.py` | 21 组用例（集成） | 新 |

---

### Task 0: 前提确认（已确认）

- [x] `report_forms` / `report_items` 已在 0001 建好（列、唯一约束、CHECK 齐全），**本卡只加序列**；
- [x] `services/audit.py::append_audit` 可用（S4-7），审计行随本卡接入业务；
- [x] `services/repository.py` 已登记 `ReportForm`（submitter 本人 / reviewer 仅 submitted 之后 / admin none）；
- [x] `page_size` 上限 100、`sort_by` 白名单机制（S2-4）可直接复用。

### Task 1: 迁移 0008 + 枚举

- [x] `enums.AuditAction` 末尾追加 `REPORT_CREATE = "report_create"`（顺序即 CHECK 顺序）；
- [x] 迁移 0008：`op.execute("CREATE SEQUENCE report_no_seq START 1")`；
      `drop_constraint/create_check_constraint ck_audit_logs_action`，值 = 旧 13 个 + `report_create`；
- [x] downgrade：先查 `audit_logs` 是否已有 `report_create` 行（有则拒绝，审计不可删），再还原 CHECK、`DROP SEQUENCE`；
- [x] 验证：`alembic upgrade head` / `alembic check` / `alembic downgrade 0007 && upgrade head` 往返。

### Task 2: `schemas/report.py`

- [x] `ReportItemOut{id, invoice_id, amount→str, remark}`；
- [x] `ReportCreateRequest{title:1-100, reason:1-500}`；
- [x] `ReportItemCreateRequest{invoice_id, amount: Decimal(12,2)≥0, remark≤200}`（三位小数由 Pydantic 拒绝）；
- [x] `ReportOut{id, report_no, applicant_id, department(派生), title, reason, status, total_amount→str,
      item_count, digest_algo, digest_hex, submitted_at, created_at, updated_at, items[]}`；
- [x] `SignatureOut` / `EnvelopeOut`（**无** `signature` 私钥、**无** `ciphertext_b64`/`wrapped_key_b64`/`ciphertext_storage_key`；
      `recipient_cert_fp`、`wrapped_key_len` 是派生字段）；
- [x] `ReportDetailData{report, signature|null, envelope|null, verify_status}`；
- [x] `ReportListQuery(PageParams){status(8 值), date_from, date_to, sort_by(created_at|submitted_at)}`。

### Task 3: `services/report_service.py`

- [x] `_next_report_no(db)`：`nextval('report_no_seq')` + `Asia/Shanghai` 业务月 + 4 位补零；
- [x] `transition(db, form, target, actor)`：8 值校验 → 幂等 → 允许表 → 改状态 + `version+1` + flush；
- [x] `create_report(db, user, payload)`：角色闸门（admin 404 / 非 submitter 403）→ 插单 →
      `append_audit(report_create)` → commit → `ReportOut`；
- [x] `add_item(db, user, report_id, payload)`：可见性 404 → 角色 → 单据状态 → **票据行 `FOR UPDATE`** →
      票据状态分诊（confirmed/in_report/其它）→ 金额校验（`42201` + `data.expected`）→ 同单/跨单重复（`40901`）→
      插明细 → 票据 `in_report` + `version+1` → 重算合计/条数 → commit；
- [x] `get_report_detail`：可见性 → items / active signature / form envelope → `verify_status` 派生；
- [x] `list_my_reports`：仅 submitter（403）→ `filter_visible` → `status`/业务日筛选 → 白名单排序（`nulls_last`）；
- [x] 捕获 `IntegrityError`（`uq_report_items_form_invoice`）转 `409/40901`。

### Task 4: `api/v1/reports.py` + `main.py`

- [x] `POST /reports` 201、`POST /reports/{id}/items` 201、`GET /reports/{id}` 200、`GET /reports/mine` 200；
- [x] `/mine` 注册在 `/{report_id}` 之前；
- [x] 路由 ≤30 行：`ok(service(...))`，不写业务；
- [x] `main.py` 挂载 router。

### Task 5: 测试 `test_reports.py`

- [x] 模块级临时库 + `command.upgrade(head)`（复用 `tests/db_helpers.py`）；
- [x] 夹具：四个账号（alice/bob/rita/ada）+ 已确认票据工厂 + 客户端；
- [x] 按 spec §8 的 21 组用例逐条落地（含并发建单 10 线程、并发同票两单、同单并发合计不丢更新、回滚复原、状态机）；
- [x] 并发用例用独立 Session + `ThreadPoolExecutor`，断言"恰好一个成功"。

### Task 6: 真实跑验收

- [x] `pytest tests/test_reports.py -m integration -q`；
- [x] 全量：`pytest -q`（默认）、`pytest -m integration -q`；
- [x] `alembic check` 零差异；
- [x] 数字与失败次数写进 PROGRESS（不粉饰）。

### Task 7: 收口

- [x] API.md：S5-1 四条接口备注补"实现完成"与单号/审计口径；
- [x] SCHEMA.md §9.19b 增加 `report_create`（注明 D-084）；
- [x] DECISIONS **D-084**（单号序列口径 + 一票一单的应用层兜底 + report_create 枚举）；
- [x] PROGRESS：勾选 S5-1、补产出/验收/变更日志；
- [x] AGENTS §10.8 增加"报销单与明细"锚点；
- [x] HANDOFF：下一步 S5-2、库表行数、命令；
- [x] 提交 `feat(s5-1): 报销单创建与明细 + 单号序列与 report_create 审计`（push 由用户执行）。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 并发建单重号 | PG 序列 `nextval` + `uq_report_forms_report_no`；10 线程用例 |
| 同一票进两单 | 票据行 `FOR UPDATE` 串行化 + 票据状态分诊；并发用例断言恰好一个成功 |
| 金额 float 漂移 | 全程 `Decimal`；Pydantic 限两位小数；合计 `sum(Decimal)` |
| 建单成功但审计失败 | 同事务：一起回滚，不出现"有单无审计" |
| 跨单约束只靠应用层 | 论文"不足"如实写；测试覆盖并发路径；不新增 SCHEMA 列 |
| `nulls_last` 改动影响旧列表 | 参数默认 False，旧调用零行为变化；全量单测回归 |