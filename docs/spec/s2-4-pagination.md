# S2-4 分页、筛选与排序 — 设计（spec）

> 任务卡：S2-4 ｜ 契约：`docs/API.md` §0（分页/排序协议）§2 列表接口 ｜ 上游：S2-3（`filter_visible`）｜ 下游：S2-5 票据列表、S5 报销单/财务列表
> 状态：**已实现（2026-11-19）**；§7 的取舍已按方案 A 落地，实测见 §6。

## 1. 目标与范围

把"列表接口"收敛成**一套可复用的协议**：参数校验（page/page_size/sort_by/order）、
白名单排序、同一 where 下 count + 数据查询、稳定的分页边界；并在 5 万行数据上实测 p50/p95。

**做**：
- `schemas/common.py`：`PageParams`（Pydantic，FastAPI 可直接 `Annotated[PageParams, Query()]`）+ `PageResult[T]`；
- `services/pagination.py`：`apply_sort(...)` / `paginate(...)`（count 与数据共用同一个 `Select`）；
- `utils/text.py`：`escape_like(term)`（`q` 的 `%`/`_` 转义，防用户把通配符当输入）；
- `tests/test_pagination.py`：单元（参数边界/白名单/排序方向）+ 集成（真实库：总数、翻页、无重叠、N+1、越权交叉）；
- `scripts/bench_list.py`：临时库造 5 万行，跑三组场景打印 p50/p95，并做 `EXPLAIN ANALYZE` 索引检查。

**不做**：
- 不加生产列表端点（`GET /invoices` 属 S2-5；S2-4 只交付协议与验证）；
- 不做前端分页组件（S2-11）；
- 不做全文检索（`q` 是 `ILIKE` 子串匹配，不建 GIN/trgm 索引；5 万行实测够用，真实规模再评估）。

## 2. 契约口径

| 项 | 落地 |
|---|---|
| 入参 | `page` 默认 1、≥1；`page_size` 默认 20、1–100；越界/非整数 → `422/42200` |
| 排序 | `sort_by`（各接口白名单）+ `order`（`asc`/`desc`，默认 `desc`）；非白名单 → `422/42200` 并列出可选值 |
| 出参 | `{items, total, page, page_size}` |
| 默认排序 | 各接口自己给（票据列表 = `created_at desc`）；分页层提供 `default_sort` 参数 |
| 超末页 | `items=[]`、`total` 不变（不报错） |
| 稳定性 | 排序键相同（如 `created_at` 同秒）时按 `id asc` 兜底，翻页不会重复/漏行 |
| count | 与数据查询**共用同一个 `Select`**：`select(count).select_from(stmt.order_by(None).subquery())`，禁止另拼 where |

## 3. 设计

```python
# schemas/common.py
class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    sort_by: str | None = None
    order: Literal["asc", "desc"] = "desc"
    @property
    def offset(self) -> int: ...

class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
```

```python
# services/pagination.py
SortMap = Mapping[str, InstrumentedAttribute[Any]]

def apply_sort(stmt, model, sort_map, params, *, default: str | None = None) -> Select
def paginate(db, stmt, model, sort_map, params, *, default: str | None = None) -> PageResult
```

- `apply_sort`：`sort_by or default` → 白名单取列；取不到抛 `ValidationFailedError`（42200）；
  追加 `model.id.asc()` 作为稳定排序的第二键。
- `paginate`：先排序（排序进入同一个 stmt），再 count（去掉 order_by 的同一个 stmt 子查询），
  再 `offset/limit` 取页。**两条查询的条件天然一致**，不存在"count 与列表筛选不一致"。
- `PageResult.items` 可以直接放 ORM 对象；端点用 `PageResult[InvoiceOut]` 作为 `response_model`
  时由 Pydantic 的 `from_attributes` 完成转换（S2-5 落地）。

## 4. 筛选与 `q` 的安全

- 具体筛选项（`status`、`ticket_type`、`date_from/date_to`、`q`）**由各端点组装** `where`，
  分页层不猜业务；端点把过滤后的 `Select` 交给 `paginate` 即可。
- `q` 走 `ILIKE`：用户输入必须转义 `%`、`_`、`\` 后再拼 `%term%`，否则用户输入 `%` 会变成通配符；
  `utils/text.py::escape_like` 负责这一步（`ESCAPE '\'`）。
- 排序字段**永不接受**用户传的列名：只接受白名单 key（`created_at` / `invoice_date` / `total_amount`），
  映射到真实列由代码写死。

## 5. 测试设计

**单元（不连库）**
- `PageParams`：默认值；`page=0`/`page=-1`、`page_size=0/101/1000`、`order=bogus` 全部 ValidationError；
  `page_size=100`、`page=2` 合法；`offset` 计算正确；
- `apply_sort`：白名单命中 → 方向正确；未知 key → `ValidationFailedError(42200)` 且消息列出可选值；
  默认排序生效；`id` 兜底键存在（编译 SQL 检查）；
- `escape_like`：`%`/`_`/`\` 被转义，普通字符不变。

**集成（临时库 `study_ocr_page_<hex>`）**
- 分页正确性：25 行 → page_size=10 的三页并集等于全集且无重叠；第 4 页 items 为空、total 不变；
- count 同一条件：只查 `status='confirmed'` 时 `total` 等于过滤后行数（不是全表）；
- 排序：`total_amount desc/asc` 顺序正确；`sort_by` 非白名单 → 42200；
- 与 S2-3 组合：submitter 分页只见自己的行（total 也按过滤后算），reviewer 见全量；
- N+1：列表用**显式 join** 一次取回 `invoice_files` 字段，事件监听器断言每页 SQL 语句数 == 2
  （count + 数据），不随行数增长；
- HTTP 边界（测试专用路由，`Annotated[PageParams, Query()]`）：`page=0` → 422、`page_size=1000` → 422、
  正常参数 → 200 且 `{items,total,page,page_size}` 形状正确。

## 6. 5 万行基准（`scripts/bench_list.py`）

- 一次性临时库 `study_ocr_bench_<hex>`：迁移 → 1 个 submitter + 1 个 reviewer + **50,000** 张票据
  （`generate_series` 批量插入，`invoice_no/code` 用 id 补齐，覆盖 `q` 过滤）；
- 场景（每组跑 `--iterations` 次，默认 50）：
  1. submitter 本人 + `status=draft` + `created_at desc` + 第 1 页；
  2. submitter 本人 + `status=draft` + `created_at desc` + 深页（page≈1000）；
  3. reviewer 全量 + `total_amount desc` + 第 1 页；
  4. submitter 本人 + `q`（发票号子串）+ `created_at desc`；
- 输出每组 p50/p95、count 与数据耗时；`--threshold-ms 300` 超阈值退出码 1；
- `EXPLAIN ANALYZE` 打印并检查主列表查询走索引（`ix_invoices_uploader_created` / `ix_invoices_draft`）；
- 结束后删临时库（`--keep` 可保留排查）。

**实测（2026-11-19，50,000 行 / 每组 50 次）**：

| 场景 | total | p50 | p95 |
|---|---|---|---|
| submitter + status=draft + created_at desc（首页） | 40,000 | 13.4ms | **15.9ms** |
| submitter + status=draft + 深页 page=1000 | 40,000 | 20.1ms | **25.4ms** |
| reviewer 全量 + total_amount desc | 50,000 | 19.3ms | **21.9ms** |
| submitter + q=发票号子串 | 11,114 | 27.6ms | **32.3ms** |

`EXPLAIN ANALYZE`：`Index Scan using ix_invoices_uploader_created`，Execution Time 11.5ms；
四组 p95 均 < 300ms，临时库已自动删除。

## 7. 需要点头的取舍：模型层要不要加 relationship

任务卡提示词要求"列表关联用 `selectinload/joinedload` 一次取"。S2-1 当时决定**不写 relationship**（锚点已登记），
S2-4 若为了 `selectinload` 新增 `Invoice.file` / `Invoice.template`，等于改数据模型锚点，且引入懒加载语义。

- **方案 A（推荐）**：保持无 relationship。列表用**显式 `join` + `add_columns`** 一次取回
  `invoice_files.filename/sha256/mime/size/page_count`，每页仍然只有 2 条 SQL（实测断言）；
  `paginate` 保留传入 `Select` 的 options，将来若加 relationship 可直接 `selectinload`。
- **方案 B**：现在给 `Invoice` 加 `file` / `template` 两个 relationship，列表用 `selectinload`。
  好处是 ORM 更顺手；代价是改 S2-1 锚点、要防懒加载、序列化路径变长。

我选 A，理由是当前列表需要的字段是明确的、join 一次取完就够，没必要为"写法好看"引入懒加载风险；
若你更希望 B，我在 S2-4 里一并改模型与锚点。

**已拍板（2026-11-19）**：采用 **方案 A**——保持无 relationship，列表用显式 join + `paginate(scalar=False)`；
测试用语句计数断言每页恰好 2 条 SELECT，证明没有 N+1。S2-1 的模型锚点不变。

## 8. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest tests/test_pagination.py -p no:warnings        # 单元
../.venv/Scripts/python.exe -m pytest -m integration tests/test_pagination.py -p no:warnings  # 集成（需 Docker）
../.venv/Scripts/python.exe -m pytest -p no:warnings                                # 全量单元
../.venv/Scripts/python.exe -m pytest -m integration -p no:warnings                 # 全量集成
cd .. && .venv/Scripts/python.exe scripts/bench_list.py --rows 50000 --iterations 50  # p50/p95 + EXPLAIN
```

通过标准：两套测试全绿；基准四组 p95 < 300ms；EXPLAIN 显示走索引；临时库清理干净。

## 9. 风险与回滚

| 风险 | 处理 |
|---|---|
| count 子查询在大表上变慢 | 基准实测；若慢改为 `with_only_columns(func.count())`（仍共用 where），并记录 EXPLAIN |
| 深页 offset 变慢 | 5 万行实测；若超阈值，S2-5 再评估 keyset 分页（本卡先保证协议一致） |
| 排序字段被注入 | 白名单映射 + 单测；`order` 用 Literal，只有两个值 |
| 集成测试污染开发库 | 临时库 `study_ocr_page_<hex>`，`finally` 删除 |
| 脚本 seed 50k 太慢 | 用 `generate_series` + CTE 一次插入，不逐行 ORM |

## 10. 收口

- `PROGRESS.md` 勾 S2-4 + 实测 p50/p95；`DECISIONS.md` 记 D-042（显式 join 而非 relationship；
  为什么 count 用同一 stmt 子查询；keyset 分页的取舍留待 S2-5 评估）；
- `AGENTS.md` §10.8 回填"分页与筛选"锚点；
- `HANDOFF.md` 下一步 S2-5；提交 `feat(s2-4): ...`；**push 由用户执行**。