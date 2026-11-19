# S2-3 实现计划（plan）

> 对应 spec：`docs/spec/s2-3-isolation.md` ｜ 契约：`docs/API.md` §0 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 1 — 策略与函数（`services/repository.py`）

- `VisibilityPolicy` 数据类：`owner_column` / `reviewer` / `admin` / `reviewer_condition`；
- `POLICIES`：`Invoice`、`InvoiceFile`、`ReportForm` 三条；
- `REVIEWER_REPORT_STATUSES = (submitted, approved, rejected, archived)`；
- `scope_for(model, user)`：未知模型 → `false()`；未知角色 → `false()`；
- `filter_visible(stmt, model, user)` / `select_visible(model, user)`；
- `get_visible_or_404(db, model, id, user)`：命中返回；未命中再判存在性 → `PermissionDeniedError` / `NotFoundError`。

**验证**：单元测试断言三角色 × 三模型的 SQL 片段、未登记模型 `false()`。

## Step 2 — 测试夹具（`tests/test_isolation.py`）

- 复用 `tests/db_helpers.py`：临时库 `study_ocr_iso_` + `alembic upgrade head`；
- 模块级 engine/sessionmaker；每个用例前 `TRUNCATE users RESTART IDENTITY CASCADE`；
- 数据助手：建 A/B/R/admin 四个用户 + 各自的 `invoice_files` / `invoices` / `report_forms`（含各状态）；
- HTTP 夹具：`create_app()` + 覆盖 `get_db` + 两条测试专用路由
  （`/_test/invoices`、`/_test/invoices/{id}`）；token 用 `encode_token` 直接签，不依赖 Redis。

**验证**：`pytest -m integration -q tests/test_isolation.py`。

## Step 3 — 集成用例

按 spec §5 写：
- submitter 列表/详情互相隔离（A vs B）；`PermissionDeniedError` vs `NotFoundError` 内部区分；
- reviewer 全量票据/文件；报销单只 4 个状态可见（参数化 8 状态）；
- admin 三类全 404；
- 列表与详情一致性（角色参数化循环断言）；
- `invoice_files` 隔离；
- HTTP：B 查 A → 404/40400，reviewer 200，admin 404，未登录 401，B 列表不含 A。

**验证**：`pytest -m integration -q tests/test_isolation.py` 全绿。

## Step 4 — 回归与收口

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m pytest -m integration
```

- `DECISIONS.md` 追加 D-041（RLS vs 应用层，含连接池 + `SET LOCAL` 坑与代价）；
- `PROGRESS.md` 勾 S2-3、记录实测条数；`HANDOFF.md` 下一步 S2-4；
- `AGENTS.md` §10.8 新增"行级数据隔离"锚点；
- 提交 `feat(s2-3): 行级数据隔离（统一 filter_visible + 越权 404）`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 新模型被默认拒绝导致后续接口全 404 | 在 `POLICIES` 登记该模型的可见性策略（这是设计意图，不是 bug） |
| `get_visible_or_404` 两查询在越权时慢 | 只在未命中时多一次主键查询，可接受；若热点再合并成一条 SQL + 内部标记 |
| reviewer 状态口径变化 | 只改 `REVIEWER_REPORT_STATUSES`，测试同步参数化 |