# S2-4 实现计划（plan）

> 对应 spec：`docs/spec/s2-4-pagination.md` ｜ 契约：`docs/API.md` §0 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 1 — 参数与结果模型（`schemas/common.py`）

- `PageParams`：`page=1 (ge=1)`、`page_size=20 (1..100)`、`sort_by: str | None`、`order: Literal["asc","desc"]="desc"`、
  `offset` 属性；
- `PageResult[T]`：`items/total/page/page_size`；`Generic` 与 `Envelope` 一致。

**验证**：`pytest tests/test_pagination.py -k "page_params"`。

## Step 2 — 分页与排序服务（`services/pagination.py`）

- `SortMap = Mapping[str, InstrumentedAttribute[Any]]`；
- `apply_sort(stmt, model, sort_map, params, *, default=None)`：白名单 → 列；未知 key →
  `ValidationFailedError`（42200，消息列出可选值）；`id.asc()` 兜底；
- `paginate(db, stmt, model, sort_map, params, *, default=None)`：
  `count = select(func.count()).select_from(stmt.order_by(None).subquery())`；
  `rows = db.scalars(stmt.offset(...).limit(...)).all()`；
- `utils/text.py::escape_like`：转义 `\`、`%`、`_`，返回 `%term%` 可直接用于 `ilike(..., escape="\\")`。

**验证**：单元测试覆盖参数边界、白名单、方向、`id` 兜底、转义。

## Step 3 — 测试（`tests/test_pagination.py`）

- 单元：见 spec §5 第一组；
- 集成：临时库 `study_ocr_page_` + 迁移；造 25 行 + 若干 `invoice_files`；
  - 三页并集 = 全集、无重叠、超末页空；count 同一条件；排序 asc/desc；
  - 与 `select_visible` 组合（submitter/reviewer total 不同）；
  - N+1：显式 join 取 file 字段，`event.listen(engine, "before_cursor_execute")` 统计 SELECT 数 == 2；
  - HTTP 边界：测试专用路由 `Annotated[PageParams, Query()]`，page=0/page_size=1000 → 422，正常 → 200。

**验证**：`pytest -m integration -q tests/test_pagination.py`。

## Step 4 — 基准脚本（`scripts/bench_list.py`）

- 参数：`--rows`（默认 50000）、`--iterations`（50）、`--threshold-ms`（300）、`--keep`；
- 临时库建/删、`alembic upgrade head`、`generate_series` 批量 seed（文件 50k + 票据 50k + invoice_no/code 回填）；
- 四组场景跑 p50/p95（见 spec §6），打印 count/数据耗时；
- `EXPLAIN ANALYZE` 主列表 SQL，检查 `Index Scan`/`Bitmap Index Scan` 与索引名；
- 超阈值退出码 1；清理临时库。

**验证**：`cd .. && .venv/Scripts/python.exe scripts/bench_list.py` 输出 p50/p95 与计划。

## Step 5 — 回归与收口

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m pytest -m integration
```

- `DECISIONS.md` 追加 D-042（显式 join 而非 relationship；count 用同一 stmt 子查询；keyset 留待评估）；
- `PROGRESS.md` 勾 S2-4、贴 p50/p95；`HANDOFF.md` 下一步 S2-5；
- `AGENTS.md` §10.8 回填"分页与筛选"锚点；
- 提交 `feat(s2-4): 分页/排序协议与 5 万行基准`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| p95 超 300ms | 先看 EXPLAIN：缺索引 → S2-1 补索引迁移（需用户确认）；count 慢 → 改 `with_only_columns`；仍慢 → 记 keyset 方案给 S2-5 |
| 深页 offset 慢 | 5 万行通常可接受；超阈值则先限 `page` 上限并记录，keyset 在 S2-5 评估 |
| 白名单误拒合法字段 | 各端点维护自己的 `SortMap`；测试逐字段断言 |
| 集成测试污染开发库 | 临时库 `study_ocr_page_`，`finally` 删除 |