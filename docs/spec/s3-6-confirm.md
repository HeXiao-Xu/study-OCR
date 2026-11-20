# S3-6 字段修正与整单确认 — 设计（spec）

> 任务卡：S3-6 ｜ 契约：`docs/API.md` §2 `PATCH /invoices/{id}/fields`（已实现）、
> `POST /invoices/{id}/confirm`（未实现）｜ 上游：S2-5（字段修改/乐观锁）、S3-2（need_review/review_level）、
> S3-4（`FieldError`）、S3-5（详情页）｜ 下游：S5（报销单只用 confirmed 票据）
> 状态：**已实现（2026-11-20）**；实测：真实票首次 confirm **422**（5 字段）→ 修复后 **200 confirmed**
> （version 0→3）；同 version 并发 PATCH 第二次 409；单元 422 / 集成 222 全绿，无迁移。

## 1. 目标与范围

把"人工改字段 → 整单确认"闭环补完：`PATCH /fields` 已在 S2-5 就绪（不重写），本卡实现
`POST /invoices/{id}/confirm`——服务端重跑校验，必填齐备、无未决冲突、低置信字段均已人工确认，
才把 `status` 与 `extract_status` 一起置 `confirmed`；否则 `422/42201` + 逐字段错误。

**做**：

| # | 产出 | 说明 |
|---|---|---|
| 1 | `services/rule_source.py`（新） | 把 `ocr_pipeline._resolve_rules` 提为共享函数：DB active 模板优先、YAML 回退 |
| 2 | `services/ocr_pipeline.py` | 改为调用 `rule_source.resolve_rules`（行为不变） |
| 3 | `services/invoice_service.py` | `confirm_invoice(db, user, invoice_id, *, version=None) -> InvoiceOut` |
| 4 | `api/v1/invoices.py` + `schemas/invoice.py` | `POST /{id}/confirm`（可选 `{version}`），`ConfirmRequest` |
| 5 | `web/src/views/InvoiceDetail.vue` + `api/invoices.ts` + `types/invoice.ts` | "确认整单"按钮；422/42201 逐字段错误展示 |
| 6 | `backend/tests/test_confirm.py`（新） | 闸门/权限/幂等/并发 409/422 字段明细 |
| 7 | `docs/API.md` §2 | confirm 实现标记与备注（闸门口径、权限、审计延后） |

**不做**：重写 `PATCH /fields`（S2-5 已实现，卡上重复描述不重复落地）；审计**行**（S4-7 哈希链未实现，
现在写=伪造 hash，用户已确认）；报销单（S5）；`POST /invoices/{id}/reocr` 权限放宽（遗留 #55，仍待定）。

## 2. 已拍板与偏离（用户 2026-11-20 确认）

| # | 问题 | 裁决 |
|---|---|---|
| Q1 | confirm 谁能调用 | **按契约"本人"**：仅 `uploader_id == user.id`；reviewer → 403，admin → 404（repository）；审核在 S5 |
| Q2 | "低置信项均已确认"怎么判 | **必须逐字段确认**：`need_review=true 且 confirmed=false` → 422 列出，用户先 `PATCH /fields` 带 `confirm=true` |
| Q3 | 任务卡要求写 `audit_logs(before/after)` | **留 S4-7**：哈希链未实现，本卡只打应用日志（D-040/D-061 同口径，D-065） |

其余我定的取舍：

| # | 冲突/风险 | 处理 |
|---|---|---|
| C1 | 任务卡权限句自相矛盾（"reviewer 可改值但不能改金额（金额修改需 reviewer）"） | 沿用 **D-017**：submitter 不能改金额；reviewer 可改全部字段，改金额需 `confirm_amount=true`；与 API.md PATCH 备注一致，不改 |
| C2 | 任务卡"仅 draft/needs_review 态" | PATCH 现状已允许 `status=draft` 且 `extract_status ∈ {raw, need_confirm}`；**保持**（契约 PATCH 错误码同义） |
| C3 | "必填齐全"的必填集从哪来 | 用 `rule_source.resolve_rules(db, invoice_type)`：DB active 模板 `field_specs_json` 的 `required=true` 且适用于该票种；YAML 回退（与流水线同源，防两套规则漂移） |
| C4 | confirm 的乐观锁语义 | 与 PATCH 一致：可选 `version` 不匹配 → 409；通过后 `version += 1`（同一次提交） |
| C5 | 校验错误如何归类到字段 | `run_validators` 的 `severity=error` issue → `{field_name, error: message}`；同一字段多条只留第一条（响应可读） |

## 3. 设计

### 3.1 `services/rule_source.py`（共享规则来源）

```python
"""抽取/校验规则的唯一来源：DB active 模板优先，库缺该票种才回退 YAML。"""
@dataclass(frozen=True)
class ResolvedRules:
    rules: list[FieldRule]
    source: str                  # "db" | "yaml"
    template_id: int | None

def resolve_rules(db: Session, ticket_type: str, *, registry=None) -> ResolvedRules:
    reg = registry or template_registry.registry
    snapshot = reg.active_templates(db).get(ticket_type)
    if snapshot is not None and snapshot.field_specs_json:
        return ResolvedRules(rules_from_specs(snapshot.field_specs_json), "db", snapshot.id)
    return ResolvedRules(load_field_rules(), "yaml", None)
```

`ocr_pipeline._resolve_rules` 改为 `resolved = resolve_rules(db, ticket_type, registry=deps.registry)`；
`metrics.rules_source/template_id` 取值不变（回归测试钉住）。

### 3.2 `confirm_invoice`

```python
def confirm_invoice(db, user, invoice_id: int, *, version: int | None = None) -> InvoiceOut:
```

1. `invoice = get_visible_or_404(...)`（admin 已被 repository 判 404）；
2. **非本人** → `RoleForbiddenError`（403/40301，契约"本人"）；
3. **幂等**：`status == confirmed and extract_status == confirmed` → 直接返回当前 `InvoiceOut`（200）；
4. `status != draft` → `ConflictError`（409）；
5. `version is not None and version != invoice.version` → `ConflictError`（409）；
6. 取 `rows = {field_name: row}`；`ticket_type = invoice.invoice_type`；`rules = resolve_rules(...).rules`；
7. **闸门**（按字段聚合，去重保序）：
   - **必填缺失**：`rule.required and rule.applies_to(ticket_type)` 且（行不存在 或
     `normalized_value/field_value` 为空）→ `必填字段缺失`；
   - **未决冲突**：`row.conflict=True` → `存在未决冲突，请先选择候选`；
   - **低置信未确认**：`row.need_review=True and not row.confirmed` → `低置信字段尚未人工确认`；
   - **校验失败**：`run_validators({name: normalized_value or field_value}, ticket_type)` 的
     `severity=error` issues → `issue.message`；
   - 同一字段多条错误只保留第一条（顺序：必填 → 冲突 → 低置信 → 校验）；
8. 有错误 → `raise UnresolvedConflictError("整单校验未通过", detail={"fields": [{"field_name": ..., "error": ...}]})`
   （HTTP 422/42201，`data.fields` 与契约一致）；**事务不落任何修改**；
9. 通过 → `invoice.status = confirmed`、`invoice.extract_status = confirmed`、`invoice.version += 1`，
   `db.commit()`，记录应用日志（`event=invoice_confirmed`，不含字段值）；
10. 返回 `_invoice_out(invoice, file)`（与列表/详情同一序列化）。

### 3.3 接口

```http
POST /api/v1/invoices/{id}/confirm
Content-Type: application/json        # 可省略 body

{"version": 3}                        # 可选；不传则不做乐观锁校验
```

- 200：`data = InvoiceOut`（`status=confirmed`、`extract_status=confirmed`、`version` 已 +1）；
- 403/40301：非本人；404/40400：不可见；409/40900：状态不允许或版本冲突；
- 422/42201：`data.fields = [{field_name, error}]`（闸门未过，`message="整单校验未通过"`）。

### 3.4 前端

- `api/invoices.ts::confirmInvoice(invoiceId, version?)` → `unwrap`；
- `InvoiceDetail.vue`：`canEdit` 为真且 `status=draft` 时显示 **"确认整单"**（主按钮，二次确认
  `ElMessageBox.confirm`）；成功 → `load()` 并提示"已确认"；
- 42201：把 `data.fields` 渲染成卡片内 `el-alert`（红色，列出字段中文名 + 原因），并 `ElMessage.error('整单校验未通过，请按提示处理')`；
  切换/刷新后清空；不覆盖已有字段输入（无编辑态时无输入）。
- 只读用户（reviewer）看不到该按钮（后端也 403）。

## 4. 测试设计

**集成（临时库 + HTTP；`tests/test_confirm.py`）**
- happy：字段齐全、全部 auto/已确认、校验通过 → 200，`status=confirmed`、`extract_status=confirmed`、`version` +1；
- 幂等：再次 confirm → 200，版本不再 +1；
- 必填缺失：删掉 `invoice_no` 行 → 422/42201，`data.fields` 含 `invoice_no`；
- 未决冲突：某行 `conflict=true` → 422 且字段名列出；
- 低置信未确认：`need_review=true, confirmed=false` → 422；用 `PATCH /fields {confirm:true}` 后 → 200；
- 校验失败：`amount/tax/total` 不自洽 → 422 `total_amount`；
- 权限：他人 submitter 404、reviewer 403、admin 404；
- 状态：`status=confirmed` 但 `extract_status != confirmed`（手工构造）→ 409；`void` → 409；
- 版本冲突：传旧 `version` → 409；
- **并发写（任务卡验收）**：两个 PATCH 用同一 `version=0`，第一个 200（version→1），第二个 409/40900。

**单元**
- `rule_source.resolve_rules`：DB 有模板 → source=db/template_id；库空 → yaml（清 registry 缓存）；
- 闸门聚合函数（抽成纯函数 `_collect_confirm_errors(rows, rules, ticket_type) -> list[dict]`）：
  字段顺序稳定、同字段去重、四类原因优先级。

**前端**
- `npm run typecheck && npm run build`；
- 冒烟：submitter 打开待确认票据 → 点"确认整单" → 缺必填时出现逐字段错误列表；补齐并逐字段确认后再确认 → 状态变"已确认"。

## 5. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check          # 本卡无迁移（head 0005）
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
# 真实冒烟（uvicorn + worker + Vite + 临时 submitter）：上传 → 确认 → 422 字段 → 逐字段确认 → 200
```

## 6. 收口

- `PROGRESS` 勾 S3-6 + 实测（confirm 闸门、并发 409、422 字段）；关闭遗留 #29（42201 过严的评估结论）、
  #46 的"批量确认"部分；新增遗留：`confirm_field` 审计行待 S4-7、reviewer 复核确认待 S5；
- `DECISIONS`：**D-065**（confirm 闸门口径：必填/冲突/低置信逐字段确认/校验器四类；本人权限；
  版本 +1；审计行留 S4-7；不重写 PATCH）；
- `API.md` §2 confirm 标记已实现；`AGENTS` §10.8 锚点；`HANDOFF` 下一步 S3-7；
- 提交 `feat(s3-6): ...`（push 由用户）。