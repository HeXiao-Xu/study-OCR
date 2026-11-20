# S3-9 双人复核：提交人全字段 + 审核人二审 — 设计（spec）

> 用户 2026-11-20 直接需求（卡片外补充）：提交人可**全部字段**编辑/确认/整单确认；审核人**再确认一次**，
> 且审核人可改字段。本卡取代 D-017 的"提交人不能改金额"限制。
> 契约：`API.md` §2 PATCH / confirm、本卡新增 `POST /invoices/{id}/review`；`SCHEMA.md` 两列（迁移 0007）
> 状态：**已实现（2026-11-20）**；实测：提交人改金额（勾选）→ 确认 v4 → 审核人复核 200（v5、`reviewed_by/at`）→
> 复核后改字段 409、重复复核 200 且版本不变；列表 pending/reviewed 正确；单元 422 / 集成 245 全绿；迁移 0007。

## 1. 目标

把现在的"提交人不能碰金额 → 点确认 403"半套流程，改成**明确的两级确认**：

```
上传 → OCR → 提交人核对/改任意字段（金额改值需勾选）→ 提交人整单确认（一审，status=confirmed）
                                                        ↓
                              审核人核对/改任意字段 → 复核通过（二审，reviewed_at 落库）
                                                        ↓
                                          票据进入"已复核"，可被 S5 报销单引用
```

- **提交人**：全部字段可改（含金额，改金额勾"确认修改金额"）、可逐字段确认、可整单确认；
- **审核人**：全部字段可改（金额同样要勾选）、可逐字段确认、可**复核通过**；不能整单确认（保持 D-065 的"本人"）；
- **复核即锁定**：`reviewed_at` 非空后字段不可再改、不可重复复核（幂等返回）；
- **不新增 status 枚举值**：待复核 = `status='confirmed' AND reviewed_at IS NULL`。

## 2. 已拍板（用户 2026-11-20）

| # | 问题 | 裁决 |
|---|---|---|
| Q1 | 二审位置 | **票据级**：`reviewed_by/reviewed_at` + `POST /invoices/{id}/review`（迁移 0007） |
| Q2 | 金额勾选 | **保留** `confirm_amount=true`：任何人改金额都要勾选（留痕语义），二审是额外复核 |
| Q3 | 复核后回退 | **复核即锁定**：不可再改、不可反复核；后续要驳回走 S5 报销单流程 |

## 3. 数据（迁移 0007）

```sql
ALTER TABLE invoices ADD COLUMN reviewed_by BIGINT NULL REFERENCES users(id);
ALTER TABLE invoices ADD COLUMN reviewed_at TIMESTAMPTZ NULL;
CREATE INDEX ix_invoices_reviewed_at ON invoices (reviewed_at);
```

- `reviewed_by`/`reviewed_at` 同进同出（CHECK 不必加；应用层保证）；
- SCHEMA.md 两表/列同步；`alembic check` 零差异；downgrade 可删。

## 4. 权限矩阵（新）

| 操作 | submitter | reviewer | admin |
|---|---|---|---|
| 改任意字段（含金额，需 `confirm_amount`） | ✅ | ✅ | 404 |
| 逐字段"确认"（`confirm=true`） | ✅ | ✅ | 404 |
| 整单确认 `POST /confirm` | ✅（本人） | ❌ 403 | 404 |
| 复核 `POST /review` | ❌ 403 | ✅ | 404 |
| 已复核后改字段 | ❌ 409 | ❌ 409 | 404 |

> `PATCH /fields` 的"已复核锁定"检查放在状态检查之后、版本检查之前（409）。

## 5. 接口

### 5.1 `PATCH /invoices/{id}/fields`（行为变更）

- 删除"submitter 改金额 → 403"；
- 金额字段改动：**任何角色**都必须 `confirm_amount=true`，否则 `422/42202`；
- 票据已复核（`reviewed_at` 非空）→ `409/40900`"已复核，不可修改"；
- 其余不变（乐观锁、42201 冲突回滚、`source=manual`、`extract_status=need_confirm`）。

### 5.2 `POST /invoices/{id}/review`（新增）

- 权限：`reviewer`；submitter → 403；admin → 404；不可见 → 404；
- 入参：可选 `{version}`（乐观锁，同 confirm）；
- 出参 **200**：`InvoiceOut`（新增 `reviewed_by`、`reviewed_at`）；
- 闸门（服务端，用库内当前值）：
  1. `status` 必须 `confirmed`，否则 `409`；
  2. 已复核（`reviewed_at` 非空）→ **幂等 200**（版本不增）；
  3. 重跑 `collect_confirm_errors`（必填/冲突/低置信未确认/validators）：有错 → `422/42201` + `data.fields`；
- 通过：`reviewed_by=user.id`、`reviewed_at=now()`、`extract_status='confirmed'`、`version += 1`，一次提交；
- 审计行仍不写（S4-7 哈希链，D-065 同口径）。

### 5.3 列表过滤

`GET /invoices?review_status=pending|reviewed`（可选）：
- `pending` = `status='confirmed' AND reviewed_at IS NULL`；
- `reviewed` = `reviewed_at IS NOT NULL`；
- 与现有 status/extract_status/type 过滤可叠加；reviewer 的"待复核"队列 = `review_status=pending`。

### 5.4 `InvoiceOut`

新增 `reviewed_by: int | null`、`reviewed_at: datetime | null`（只读，不返回审核人姓名）。

## 6. 前端

- **提交人**：金额字段现在可编辑；保存前仍要求勾"确认修改金额"；整单确认后显示"待复核"标签；
- **审核人**：详情页在 `status=confirmed AND reviewed_at IS NULL` 时显示 **"复核通过"** 按钮（二次确认弹窗）；
  复核成功后显示"已复核"标签 + 复核时间；提交人不再显示"待复核"；
- **锁定**：`reviewed_at` 非空时隐藏"编辑字段/确认整单/逐字段确认/重识别"（后端 409 兜底）；
- **列表**：新增"复核状态"筛选（待复核/已复核）；reviewer 的 `/review` 默认 `extract_status=need_confirm` 不变，
  "待复核"作为列表筛选项；
- 状态标签：`confirmed` 且未复核 → "待复核"；已复核 → "已复核"（替换/补充现有"已确认"）。

## 7. 测试

**后端**
- 迁移 0007：upgrade/downgrade/upgrade、`alembic check`；
- `test_invoices` 改：submitter 改金额**不勾选** → 42202；勾选 → 200；reviewer 同；admin/他人 404 不变；
- `test_review.py`（新）：reviewer 200（落 reviewed_by/at、version+1）；submitter 403；admin/他人 404；
  未确认 409；已复核幂等 200（版本不变）；闸门失败 422 + `data.fields`；复核后 PATCH/逐字段确认 → 409；
- 列表过滤：pending/reviewed 与 status 叠加；submitter 只能看自己的；
- 回归：`test_confirm`（提交人确认仍 200）、`test_duplicate`、`test_batch` 不受影响。

**前端**：`typecheck`/`build`；真实冒烟：提交人改金额（勾选）→ 整单确认 → 审核人复核 → 列表/详情标签；
复核后提交人再改 → 409 提示。

## 8. 验收

```bash
cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head && ../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q && ../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
```

## 9. 收口

- `PROGRESS` 勾 S3-9（补充卡）+ 实测；关闭遗留 #32（提交人金额权限的讨论）；
- `DECISIONS`：**D-068 取代 D-017 的金额限制**（任何人可改金额，须勾选；审核人二审；复核锁定）；
- `API.md` PATCH 权限/新增 review/列表过滤/InvoiceOut；`SCHEMA.md` 两列；`HANDOFF` 下一步；
- 提交 `feat(s3-9): ...`（push 由用户）。