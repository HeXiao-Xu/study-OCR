# S3-9 双人复核实现计划（plan）

> 对应 spec：[`docs/spec/s3-9-two-level-review.md`](../spec/s3-9-two-level-review.md)（已确认）｜
> 契约：`API.md` PATCH 权限变更 + 新增 `POST /invoices/{id}/review` + 列表过滤 + `InvoiceOut` 两字段 |
> 状态：**已实现（2026-11-20）**，Task 1–4 全部执行并验证

**目标**：提交人可编辑/确认**全部字段**（金额需勾选），整单确认后由审核人**复核**（二审）；
复核落 `reviewed_by/reviewed_at`、锁定修改；不新增 status 枚举。

## Global Constraints

- 只新增迁移 **0007**，不改 0001–0006；`alembic check` 零差异。
- 改 D-017 的金额限制 → 必须同步 `DECISIONS` **D-068**、`API.md`、测试；不允许"代码改了文档没改"。
- 复核锁定：`reviewed_at` 非空后 PATCH/逐字段确认一律 **409**；复核本身幂等。
- 审计行仍不写（S4-7，D-065 口径）。
- 提交 Conventional Commits（`feat(s3-9): ...`）；**push 由用户执行**。

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/alembic/versions/0007_invoice_review.py`（新） | `reviewed_by`（FK users）、`reviewed_at` + 索引 |
| `backend/app/models/invoices.py`、`docs/SCHEMA.md` | 两列 |
| `backend/app/schemas/invoice.py` | `InvoiceOut` 两字段、`InvoiceListQuery.review_status` |
| `backend/app/services/invoice_service.py` | `update_fields` 权限/锁定、`review_invoice`、列表过滤 |
| `backend/app/api/v1/invoices.py` | `POST /{id}/review` |
| `backend/tests/{test_invoices,test_review}.py` | 权限改 + 复核全套 |
| `web/src/{types/invoice.ts,api/invoices.ts,views/InvoiceDetail.vue,views/InvoiceList.vue}` | 金额可编辑、复核按钮/标签、列表过滤 |
| `docs/{API,PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` | 收口 |

---

### Task 1: 迁移 0007 + 模型 + 输出字段 + 列表过滤参数

- [ ] **Step 1: 模型**

```python
# invoices.py
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_invoices_reviewed_by"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
...
        Index("ix_invoices_reviewed_at", "reviewed_at"),
```

- [ ] **Step 2: 迁移**

```python
"""双人复核（S3-9）：invoices.reviewed_by / reviewed_at。"""
revision = "0007"
down_revision = "0006"

def upgrade() -> None:
    op.add_column("invoices", sa.Column("reviewed_by", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_invoices_reviewed_by", "invoices", "users",
                          ["reviewed_by"], ["id"])
    op.add_column("invoices", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_invoices_reviewed_at", "invoices", ["reviewed_at"])

def downgrade() -> None:
    op.drop_index("ix_invoices_reviewed_at", table_name="invoices")
    op.drop_column("invoices", "reviewed_at")
    op.drop_constraint("fk_invoices_reviewed_by", "invoices", type_="foreignkey")
    op.drop_column("invoices", "reviewed_by")
```

- [ ] **Step 3: schema**

```python
class InvoiceOut(BaseModel):
    ...
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None

class InvoiceListQuery(PageParams):
    ...
    review_status: Literal["pending", "reviewed"] | None = None
```

- [ ] **Step 4: SCHEMA.md**：`invoices` 表补两行（`reviewed_by` BIGINT FK→users、`reviewed_at` TIMESTAMPTZ，
  "二审字段，待复核=status='confirmed' AND reviewed_at IS NULL"）。

- [ ] **Step 5: 验证**

```bash
../.venv/Scripts/python.exe -m alembic upgrade head
../.venv/Scripts/python.exe -m alembic downgrade -1
../.venv/Scripts/python.exe -m alembic upgrade head
../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q tests/test_models.py tests/test_migration.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/alembic/versions/0007_invoice_review.py backend/app/models/invoices.py \
  backend/app/schemas/invoice.py docs/SCHEMA.md
git commit -m "feat(s3-9): 迁移 0007（invoices.reviewed_by/reviewed_at）与列表复核筛选参数"
```

---

### Task 2: 权限变更 + 复核服务/接口 + 测试

**Files:**
- Modify: `backend/app/services/invoice_service.py`、`backend/app/api/v1/invoices.py`
- Test: `backend/tests/test_review.py`（新）、`backend/tests/test_invoices.py`（改）

- [ ] **Step 1: `update_fields` 行为**

```python
    if invoice.reviewed_at is not None:
        raise ConflictError("该票据已复核，不可修改")
    editable = (
        invoice.status == InvoiceStatus.DRAFT.value
        and invoice.extract_status in (ExtractStatus.RAW.value, ExtractStatus.NEED_CONFIRM.value)
    )
    # 二审前审核人可继续修正：confirmed 且未复核
    reviewer_fixing = (
        invoice.status == InvoiceStatus.CONFIRMED.value
        and invoice.reviewed_at is None
        and user.role == UserRole.REVIEWER.value
    )
    if not (editable or reviewer_fixing):
        raise ConflictError(f"当前状态（{invoice.status}/{invoice.extract_status}）不允许修改字段")

    names = [item.field_name for item in payload.fields]
    ...
    if any(name in AMOUNT_FIELDS for name in names) and not payload.confirm_amount:
        raise FieldConfirmationRequiredError()      # 任何角色改金额都要勾选（D-068）
```

- [ ] **Step 2: `review_invoice`**

```python
def review_invoice(db: Session, user: User, invoice_id: int, *,
                   version: int | None = None) -> InvoiceOut:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    if user.role != UserRole.REVIEWER.value:
        raise RoleForbiddenError("只有审核人可以复核票据")
    file = db.get(InvoiceFile, invoice.file_id)
    if file is None:
        raise NotFoundError("票据文件不存在")
    if invoice.reviewed_at is not None:
        return _invoice_out(invoice, file)          # 幂等
    if invoice.status != InvoiceStatus.CONFIRMED.value:
        raise ConflictError("票据尚未整单确认，不能复核")
    if version is not None and version != invoice.version:
        raise ConflictError(f"版本冲突：当前 version={invoice.version}")

    rows = {row.field_name: row for row in db.scalars(
        select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)).all()}
    resolved = resolve_rules(db, invoice.invoice_type)
    errors = collect_confirm_errors(rows, resolved.rules, invoice.invoice_type)
    if errors:
        raise UnresolvedConflictError("复核前校验未通过", detail={"fields": errors})

    invoice.reviewed_by = user.id
    invoice.reviewed_at = datetime.now(UTC)
    invoice.extract_status = ExtractStatus.CONFIRMED.value
    invoice.version += 1
    db.commit()
    db.refresh(invoice)
    logger.info("票据复核", extra={"event": "invoice_reviewed",
                                   "invoice_id": invoice.id, "user_id": user.id})
    return _invoice_out(invoice, file)
```

（`datetime`/`UTC` 按项目现有写法导入；金额/校验只用库内值。）

- [ ] **Step 3: 列表过滤**

```python
    if query.review_status == "pending":
        stmt = stmt.where(Invoice.status == InvoiceStatus.CONFIRMED.value,
                          Invoice.reviewed_at.is_(None))
    elif query.review_status == "reviewed":
        stmt = stmt.where(Invoice.reviewed_at.is_not(None))
```

- [ ] **Step 4: 路由**

```python
@router.post("/{invoice_id}/review", response_model=Envelope[InvoiceOut],
             summary="复核（审核人二审；幂等）")
def review_invoice(invoice_id: int, payload: ConfirmRequest | None = None,
                   user=Depends(get_current_user), db=Depends(get_db)) -> dict:
    return ok(invoice_service.review_invoice(
        db, user, invoice_id, version=payload.version if payload else None))
```

- [ ] **Step 5: 测试**

`tests/test_review.py`（临时库 + HTTP，照 `test_confirm.py`）：
- happy：submitter 确认后 reviewer review → 200，`reviewed_by=rita.id`、`reviewed_at` 非空、version+1；
- 权限：submitter 403、admin 404、他人 reviewer 404；
- 未确认（draft）→ 409；已复核再 review → 200 且 version 不变；
- 复核闸门：reviewer 在 confirmed 后改金额为不自洽值并确认 → review 422/42201 + `data.fields`；
- 复核后 PATCH → 409；`GET /invoices?review_status=pending|reviewed` 正确；
- reviewer 在 confirmed 未复核时 PATCH 金额（勾选）→ 200（二审前修正）。

`tests/test_invoices.py` 改：
- submitter 改金额不勾选 → **422/42202**（原 403 删除）；勾选 → 200；
- reviewer 同；已复核 PATCH → 409（可放到 test_review）。

- [ ] **Step 6: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_review.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_invoices.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_confirm.py tests/test_duplicate.py
../.venv/Scripts/python.exe -m pytest -q tests/test_api_docs.py
```

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/invoice_service.py backend/app/api/v1/invoices.py \
  backend/tests/test_review.py backend/tests/test_invoices.py
git commit -m "feat(s3-9): 提交人可改全部字段；审核人复核接口与复核锁定"
```

---

### Task 3: 前端

**Files:** `web/src/types/invoice.ts`、`api/invoices.ts`、`views/InvoiceDetail.vue`、`views/InvoiceList.vue`

- [ ] **Step 1: 类型/API**

```ts
// InvoiceOut / list item
reviewed_by: number | null
reviewed_at: string | null
// InvoiceListParams
review_status?: 'pending' | 'reviewed'

// api/invoices.ts
export async function reviewInvoice(invoiceId: number, version?: number): Promise<InvoiceOut> {
  const envelope = await post<InvoiceOut>(`/invoices/${invoiceId}/review`, { version }, { silent: true })
  return unwrap(envelope)
}
```

- [ ] **Step 2: 详情页权限/交互**

- `canEdit`：`draft && extract_status in (raw, need_confirm) && !reviewed_at`；
  或 `reviewer && status==='confirmed' && !reviewed_at`；
- `fieldEditable`：去掉"submitter 不能改金额"分支；
- 金额勾选框：`editMode && hasEditableAmount`（任何角色）；保存时 `amountChanged` 就传 `confirm_amount: true`
  （提交人也一样）；
- "复核通过"按钮（`reviewer && status==='confirmed' && !reviewed_at`）：
  二次确认 → `reviewInvoice(id, version)` → 成功 reload；42201 展示 `confirmErrors`；
- 状态标签：`reviewed_at` 非空 → "已复核"；`confirmed && !reviewed_at` → "待复核"（否则沿用原标签）；
- `reviewed_at` 非空：隐藏编辑/确认/逐字段确认/重识别（`canEdit=false`）；
- 复核人信息用 `reviewed_at` 时间展示（不显示姓名，避免返回敏感字段）。

- [ ] **Step 3: 列表筛选**

`InvoiceList.vue`：
- 新增"复核状态"下拉（待复核/已复核/全部），写入 `route.query.review_status`；
- 请求参数带上 `review_status`；筛选条"重置"清除；
- reviewer 的 `/review` 入口保持 `extract_status=need_confirm`（一审队列），"待复核"靠新筛选。

- [ ] **Step 4: 验证**：`npm run typecheck && npm run build`

- [ ] **Step 5: 提交**

```bash
git add web/src/types/invoice.ts web/src/api/invoices.ts \
  web/src/views/InvoiceDetail.vue web/src/views/InvoiceList.vue
git commit -m "feat(s3-9): 提交人改金额/审核人复核 UI 与待复核筛选"
```

---

### Task 4: 冒烟 + 回归 + 文档

- [ ] **Step 1: 全量验证**

```bash
cd backend && ../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
```

- [ ] **Step 2: 真实冒烟**

1. `test_sub` 打开 111 → 编辑 `amount`（勾"确认修改金额"）→ 保存 200；
2. 逐字段确认低置信项 → "确认整单" 200（状态"待复核"）；
3. `test_rev` 打开 111 → 复核通过 → 200，状态"已复核"；列表 `review_status=reviewed` 可见；
4. `test_sub` 再改字段 → 409"已复核，不可修改"；再点复核（接口）→ 200 幂等、版本不变。

- [ ] **Step 3: 文档**

- `PROGRESS`：勾 S3-9（补充卡）+ 实测；关闭遗留 #32（金额权限讨论）；
- `DECISIONS`：**D-068 取代 D-017 的金额限制**：提交人可改金额（须勾选）；审核人二审；
  复核即锁定、不新增 status 枚举；`reviewed_at` 是"待复核"的唯一依据；
- `API.md`：PATCH 权限行、`POST /review`、列表 `review_status`、`InvoiceOut` 两字段；
- `AGENTS` §10.8 锚点；`HANDOFF` 现状/下一步；spec/plan 状态改"已实现"。

- [ ] **Step 4: 提交**

```bash
git add docs AGENTS.md
git commit -m "docs(s3-9): 双人复核口径与实测收口"
```

---

## 自检（对照 spec）

| spec 条目 | Task |
|---|---|
| §3 迁移 | 1 |
| §4 权限矩阵 | 2 |
| §5.1 PATCH | 2 |
| §5.2 review | 2 |
| §5.3 列表过滤 | 1/2 |
| §5.4 InvoiceOut | 1 |
| §6 前端 | 3 |
| §7 测试 | 2/4 |
| §9 收口 | 4 |

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 不希望审核人改字段 | 只关前端入口不够，必须在 `update_fields` 去掉 `reviewer_fixing` 分支（后端是唯一权威） |
| 复核后需要改 | 走 S5 报销单驳回/作废（当前无反复核接口，刻意） |
| 待复核列表性能 | `ix_invoices_reviewed_at` 已加；数据量大时可加 `(status, reviewed_at)` 复合索引（后续） |