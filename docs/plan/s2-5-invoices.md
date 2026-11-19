# S2-5 实现计划（plan）

> 对应 spec：`docs/spec/s2-5-invoices.md` ｜ 契约：`docs/API.md` §2 / §11 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 1 — 异常与模型常量

- `core/exceptions.py`：`UnresolvedConflictError`（42201/422）、`FieldConfirmationRequiredError`（42202/422）。
- `services/invoice_service.py` 定义 `INVOICE_FIELD_NAMES`（12 个）与 `AMOUNT_FIELDS`（3 个）。

**验证**：`pytest -q` 不回归。

## Step 2 — 状态机与内部建单

- `transition(db, invoice, target, actor, expected_version=None)`：
  - 目标不在允许集合 → `ConflictError`（409，消息含当前状态与目标状态）；
  - `expected_version` 不匹配 → `ConflictError`（409）；
  - 成功：改 `status`、`version+1`、commit；`void` 为终态。
- `create_draft(db, *, uploader_id, file_id, **fields)`：仅内部用；校验 file 属于 uploader；
  写 `invoices`（status=draft、ocr_status=pending、extract_status=raw、version=0）。

**验证**：`pytest -m integration tests/test_invoices.py -k transition`。

## Step 3 — 列表与详情

- `schemas/invoice.py`：`InvoiceOut`（含文件元数据，无 `storage_key`；金额 `field_serializer` 输出两位小数字符串）、
  `InvoiceFieldOut`、`LatestJobOut`、`InvoiceDetailData`、`InvoiceListQuery(PageParams)`。
- `list_invoices`：admin → `NotFoundError`；`select_visible(Invoice)` + 默认排除 void + 筛选 +
  join `invoice_files` + `paginate(scalar=False)` + 行 → InvoiceOut。
- `get_invoice_detail`：`get_visible_or_404`；fields 按 id；latest_job 按 created_at desc/id desc。

**验证**：列表筛选/分页/三权限 + 详情 404/字段/最新 job。

## Step 4 — 软删

- `soft_delete`：owner 校验（非本人 → `PermissionDeniedError` → 404）；`void` 幂等返回；
  `in_report/archived` → 409；否则走 `transition(..., 'void')`。

**验证**：204 / 重复 204 / 409 / 他人 404 / 删除后同号重建成功。

## Step 5 — 字段修改

- 请求/响应 schema；权限（submitter 金额 403、reviewer 金额需 confirm_amount → 42202、admin 404）；
  状态/版本闸门；逐字段规范化（金额 Decimal、日期、代码/号码位数、税号 15/18/20）；
  upsert `invoice_fields` + 同步 `invoices` 列；交叉校验（金额和/日期/代码/号码/18 位税号）；
  有未决冲突 → 42201 回滚；成功 `version+1`，返回 updated/needs_confirm/conflicts。
- 不写 `audit_logs`（S4-7）；before/after 只进响应与日志（不带明文敏感数据）。

**验证**：成功链路 + 五类失败分支 + 详情反映修改。

## Step 6 — 路由与回归

- `api/v1/invoices.py` 注册 4 条；`main.py` 挂载；
  列表 `response_model=Envelope[PageResult[InvoiceOut]]`，删除 `status_code=204`。
- 全量回归：

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m pytest -m integration
../.venv/Scripts/python.exe -m alembic check
```

## Step 7 — 收口

- `PROGRESS.md` 勾 S2-5 + 实测；`DECISIONS.md` D-043（状态机/建单/字段修改的契约边界）；
- `AGENTS.md` §10.8 新增"票据 CRUD 与状态机"锚点；
- `HANDOFF.md` 下一步 S2-6；提交 `feat(s2-5): 票据查询/软删/状态机与契约版字段修改`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 42201 误伤正常修改 | 先看是否真有未决冲突（金额和/日期/号码）；必要时只对本次修改字段判冲突并记录偏离 |
| admin 列表 404 与仓库空列表不一致 | 路由层角色闸门解决；测试钉住 404 |
| 字段修改与 S3-6 冲突 | 行为以 API.md 为准；S3-6 只加复核/审计 |
| 响应金额变浮点 | `field_serializer` 固定两位小数字符串；测试断言类型 |