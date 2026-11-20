# S3-7 重复报销检测 — 设计（spec）

> 任务卡：S3-7 ｜ 契约：`docs/API.md` §2（本卡新增 `GET /invoices/{id}/duplicate-check`）、
> §2 上传 `dedup`、§13 定稿"不做独立接口"（**本卡覆盖**）｜ 上游：S2-6（上传 sha256 去重）、
> S3-1（流水线唯一冲突处理）、S3-6（confirm）｜ 下游：S5（报销提交前再查）
> 状态：**已实现（2026-11-20）**；实测：同图再传 409/40901；同票面重编码 B → `duplicate_of=49` →
> duplicate-check **strong** → confirm **409/40900**（`data.invoice_id=49`）；单元 422 / 集成 230 全绿，无迁移。

## 1. 目标与范围

同一张票不能报两次：强重复（同票号/同影像）**在确认时阻断**，弱重复（同金额+同日期+同销方税号、
不同票号）**只提示不阻断**；检测逻辑集中在 `services/duplicate.py`，详情页给出横幅。

**做**：

| # | 产出 | 说明 |
|---|---|---|
| 1 | `backend/app/services/duplicate.py`（新） | 三维度检测（`duplicate_of`/同票号、同影像 sha256、弱重复），返回 `{level, matches}` |
| 2 | `backend/app/schemas/invoice.py` | `DuplicateMatchOut` / `DuplicateCheckData` |
| 3 | `api/v1/invoices.py` | `GET /{id}/duplicate-check`（本人或 reviewer；admin 404） |
| 4 | `services/ocr_pipeline.py` | 唯一冲突回退时写 `invoices.duplicate_of`（原来只记 metric） |
| 5 | `services/invoice_service.py` | `confirm_invoice` 增加强重复闸门 → `409` + 已有 `invoice_id` |
| 6 | 前端 | 详情页重复横幅（strong 红/weak 黄，列出匹配票据链接） |
| 7 | `backend/tests/test_duplicate.py`（新） | 三档、权限、confirm 阻断、并发唯一索引 |
| 8 | 文档 | `API.md` §2/§13、`PROGRESS`、`DECISIONS D-066`、`HANDOFF` |

**不做**：报销提交接口（S5）；跨用户人工合并票据；重复票据自动作废（保留人工判断）。

## 2. 已拍板与偏离（用户 2026-11-20 确认）

| # | 问题 | 裁决 |
|---|---|---|
| Q1 | 与 API.md D-013"弱重复不做独立接口"冲突 | **新增 `GET /invoices/{id}/duplicate-check`**，覆盖 D-013（D-066）：`{level, matches}`；上传 `dedup` 与 `duplicate_of` 保留 |
| Q2 | "同票号第二张 409"在哪返回 | **confirm 时阻断**：上传/流水线只标记（`duplicate_of` + warning，作业不失败），`POST /confirm` 见强重复 → `409/40900` + `data.invoice_id` |
| Q3 | 弱重复是否上界面 | **详情页横幅**：strong 红（给已有票据链接）、weak 黄（列出金额/日期/票号） |

其余我定的取舍：

| # | 冲突/风险 | 处理 |
|---|---|---|
| C1 | 部分唯一索引让"两张同号非 void 票"根本无法共存，第二张的票号被流水线**丢弃**（`invoice_no=None`） | 强重复不靠"第二张存了同号"判断：流水线冲突时写 `duplicate_of=<已有票 id>`；检测优先看 `duplicate_of`，其次按 `(invoice_code,invoice_no)` 查（兼容导入/历史数据） |
| C2 | `invoice_files.sha256` 有 UNIQUE 约束 + 上传去重 → 两张票不可能共享同一影像 | `kind=image` 检查保留为**纵深防御**（历史/导入数据）；正常路径它恒为 none，文档写明 |
| C3 | 弱重复可能量大（同金额+同日期+同销方） | 只返回最近 20 条，按 `created_at desc`；只给最小字段（票号/日期/金额/状态），不给姓名/税号明文 |
| C4 | 跨用户可见性 | 票据读取沿用 repository 策略（本人/reviewer）；匹配列表只给"存在重复"所需的最小字段，不泄露姓名/税号 |
| C5 | 弱重复阈值 | 三维全等（`total_amount` + `invoice_date` + `seller_tax_no` 的 `normalized_value`），不同 `invoice_no`；缺任一维不判弱重复（宁可漏报，不误伤连号票） |

## 3. 设计

### 3.1 `services/duplicate.py`

```python
@dataclass(frozen=True)
class DuplicateMatch:
    invoice_id: int
    kind: str                 # "invoice_no" | "image" | "weak"
    invoice_code: str | None
    invoice_no: str | None
    invoice_date: date | None
    total_amount: Decimal | None
    status: str
    created_at: datetime

@dataclass(frozen=True)
class DuplicateReport:
    invoice_id: int
    level: str                # "strong" | "weak" | "none"
    matches: list[DuplicateMatch]

def check_duplicates(db, invoice: Invoice, *, weak_limit: int = 20) -> DuplicateReport
```

判定顺序：

1. **强重复（票号）**：
   - `invoice.duplicate_of` 非空 → 取该票据行（若不存在或 `void` 则忽略）；
   - 否则若 `invoice_code` 与 `invoice_no` 都有值 → 查 `(invoice_code, invoice_no)` 相同、`id != self`、
     `status != 'void'` 的票据（并存场景理论上被索引挡住，留给导入/历史数据）；
2. **强重复（影像）**：取本票 `invoice_files.sha256`，查使用同 sha 文件的其他票据（同 1 的最小字段）；
3. **弱重复**：本票 `invoice_fields` 的 `total_amount / invoice_date / seller_tax_no`（`normalized_value`）
   **三者都有值**才查：同三维、`id != self`、`status != 'void'`、`invoice_no` 不同（或为空）；
   排除已在强重复里的 id；`order_by(created_at.desc()).limit(20)`；
4. **level**：有 `invoice_no`/`image` → `strong`；否则有 weak → `weak`；否则 `none`。
5. 所有查询走 `db.scalars`，不写库、不加锁（只读检测）。

### 3.2 流水线写 `duplicate_of`

`ocr_pipeline._persist` 的 `IntegrityError` 分支：在只回填非冲突列的 update 里加
`duplicate_of=<冲突已有票 id>`（按尝试写入的 `invoice_code/invoice_no` 查，`status != 'void'`），
`metrics["duplicate_of"] = id`；作业仍 `succeeded`（现状语义不变）。

### 3.3 confirm 强重复闸门

`confirm_invoice` 在版本校验之后、字段闸门之前：

```python
report = check_duplicates(db, invoice)
strong = [m for m in report.matches if m.kind in ("invoice_no", "image")]
if strong:
    raise ConflictError("该票据与已有票据重复", detail={"invoice_id": strong[0].invoice_id,
                                                     "kind": strong[0].kind})
```

→ `409/40900`，`data.invoice_id` 给前端直接跳转。

### 3.4 接口

```http
GET /api/v1/invoices/{id}/duplicate-check
```

- 权限：本人或 `reviewer`；`admin` → 404；类型：同步；幂等：是；实现：S3-7
- 出参 **200**：`data = {invoice_id, level, matches:[{invoice_id, kind, invoice_code, invoice_no, invoice_date, total_amount, status, created_at}]}`
- 错误：`404/40400`
- 备注：只读检测；`kind ∈ {invoice_no, image, weak}`；`level=strong` 时 confirm 会 409；
  `matches` 只给最小字段，不含姓名/税号

### 3.5 前端

- `api/invoices.ts::fetchDuplicateCheck(invoiceId)`、`types/invoice.ts` 对应类型；
- `InvoiceDetail.vue` 在 `load()` 后调用（失败静默，不阻塞页面）：
  - `strong` → `el-alert type=error` 标题"疑似重复票据"，逐条 `router-link` 到已有票据；
  - `weak` → `el-alert type=warning` 标题"弱重复提示（同金额/日期/销方）"，列出匹配票号/日期/金额；
  - `none` → 不渲染。

## 4. 测试设计

**`backend/tests/test_duplicate.py`（集成，临时库 + HTTP）**
- `duplicate_of` 指向已有票 → `level=strong, kind=invoice_no`；
- 同 `(code,no)` 但另一张 void → 不匹配；self 排除；
- 弱重复：两票同 `total_amount/invoice_date/seller_tax_no`、不同 `invoice_no` → `level=weak`；
  排除 `invoice_no` 相同的（那属于强重复场景，不该进 weak）；
- 三档 `none`；
- `GET /duplicate-check`：本人 200、reviewer 200、他人 404、admin 404；
- `confirm`：`duplicate_of` 非空 → `409/40900` + `data.invoice_id`；无重复 → 正常 200（回归）；
- **并发**（任务卡）：两个 session 同时把两张票写成同 `(code,no)` → 第二个 `IntegrityError`
  （唯一索引兜底）；流水线已有 `duplicate_invoice_no` 用例补断言 `invoice.duplicate_of == existing_id`；
- 影像强重复：受 `uq_invoice_files_sha256` 限制无法造出两行同 sha，改为**单元级**：
  monkeypatch `duplicate._image_matches` 返回一条 match，断言 `level=strong, kind=image`（映射逻辑）。

**回归**：`tests/test_upload.py`（同图 409 已覆盖）、`tests/test_confirm.py`、`tests/test_ocr_pipeline.py`。

## 5. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check          # 本卡无迁移（head 0005）
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
# 冒烟：A 票占号 → B 票识别同号 → B.duplicate_of=A.id → confirm B=409；弱重复票详情页黄条
```

## 6. 收口

- `PROGRESS` 勾 S3-7 + 实测；新增遗留：跨用户重复合并、弱重复人工确认（S5）；
- `DECISIONS`：**D-066**（新增 duplicate-check 覆盖 D-013；强重复 confirm 409；弱重复只提示；
  duplicate_of 由流水线写；影像重复纵深防御）；
- `API.md` §2 新增接口、§13 决策行改为"已由 S3-7 覆盖"；
- `AGENTS` §10.8 锚点；`HANDOFF` 下一步 S3-8；
- 提交 `feat(s3-7): ...`（push 由用户）。