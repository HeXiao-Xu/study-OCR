# S2-3 行级数据隔离 — 设计（spec）

> 任务卡：S2-3 ｜ 契约：`docs/API.md` §0（权限矩阵 / 越权 404）§2 §4 §12 ｜ 上游：S2-2（`get_current_user` / 角色）｜ 下游：S2-5 票据 CRUD、S5 报销单
> 状态：**已实现（2026-11-19）**；§7 的偏差已按"隔离层 + 测试专用路由"落地，实测见 §6。

## 1. 目标与范围

交付**统一的可见性过滤层**：所有票据 / 文件 / 报销单的读查询都经过同一个函数，
列表与详情共用同一谓词；资源级越权对外一律 `404/40400`（不泄露资源是否存在）。

**做**：
- `services/repository.py`：`scope_for(model, user)` / `filter_visible(stmt, model, user)` /
  `select_visible(model, user)` / `get_visible_or_404(db, model, id, user)`；
- 三张核心表的可见性策略：`invoices` / `invoice_files` / `report_forms`；
- 未登记模型**默认拒绝**（deny by default），防止新表被"忘了加隔离"；
- 单元（策略）+ 集成（临时库 + 真实 HTTP 404 语义）测试；
- `DECISIONS.md` 新增 D-041：应用层过滤 vs PostgreSQL RLS。

**不做**：
- **不加生产 API 端点**（`GET /invoices`、`GET /invoices/{id}` 属 S2-5；列表分页协议属 S2-4）；
- 不写 PostgreSQL RLS（D-041 的理由）；
- 不做字段级权限（金额字段仅 reviewer 可改属 D-017 / S3-6）；
- 不写 `audit_logs.action='forbidden'` 行（哈希链在 S4-7；D-040 已约定）。

## 2. 契约规则（API.md §0 权限矩阵）

| 模型 | submitter | reviewer | admin |
|---|---|---|---|
| `invoices` | 仅 `uploader_id = 自己` | 全部 | **404（不继承）** |
| `invoice_files` | 仅 `uploader_id = 自己` | 全部（审核要看原图） | **404** |
| `report_forms` | 仅 `applicant_id = 自己`（含草稿） | 仅 `submitted` 及之后：`submitted/approved/rejected/archived` | **404** |
| 其它模型 | 默认拒绝（未登记） | 默认拒绝 | 默认拒绝 |

补充口径：
- 角色以**数据库**为准（S2-2 已保证），`scope_for` 收到的是 `User` ORM 对象；
- `report_forms` 的 reviewer 可见集合按"仅 submitted 之后"取 4 个状态；
  `draft / pending_sign / signed / encrypted` 对 reviewer 不可见（还没提交，财务不该看）；
- admin 对业务表**没有任何行级特权**：即使数据存在也返回 404（D-017 的职责分离）；
- 模型未在策略表登记时返回 `false()`，**任何角色都查不到**——这是"新表默认安全"的兜底。

## 3. 设计

```python
# services/repository.py
@dataclass(frozen=True)
class VisibilityPolicy:
    owner_column: InstrumentedAttribute        # uploader_id / applicant_id
    reviewer: Literal["all", "none", "condition"]
    admin: Literal["all", "none"]
    reviewer_condition: ColumnElement[bool] | None = None

POLICIES = {
    Invoice:     VisibilityPolicy(Invoice.uploader_id, reviewer="all", admin="none"),
    InvoiceFile: VisibilityPolicy(InvoiceFile.uploader_id, reviewer="all", admin="none"),
    ReportForm:  VisibilityPolicy(ReportForm.applicant_id, reviewer="condition", admin="none",
                                  reviewer_condition=ReportForm.status.in_(REVIEWER_REPORT_STATUSES)),
}

def scope_for(model, user) -> ColumnElement[bool]      # 未登记 → false()
def filter_visible(stmt, model, user) -> Select        # 列表用
def select_visible(model, user) -> Select              # select(model) + 过滤
def get_visible_or_404(db, model, entity_id, user)     # 详情用
```

**详情为什么查两次库**：
1. 先按同一谓词查一次；命中就返回（正常路径一次查询）；
2. 未命中时再 `db.get(model, id)` 判断"确实不存在"还是"存在但不可见"：
   前者抛 `NotFoundError`，后者抛 `PermissionDeniedError`（全局处理器把两者都转成 **404/40400**）。
   这样既保证外部无差别，又给 S4-7 留出"越权事件"的内部语义（`action=forbidden`）。
3. **谓词只有 `scope_for` 一个来源**，列表、详情、将来的导出/批量接口都复用它。

**为什么不用"各接口自己加 where"**：任务卡明确点名的翻车点——详情改了列表忘改；
抽成一个函数后，代码评审只需要 grep `filter_visible|get_visible_or_404` 就能确认覆盖面。

## 4. 文件清单

```
backend/app/services/repository.py       新增：策略表 + 4 个公开函数
backend/tests/test_repository.py         新增：单元（谓词/SQL，不连库）
backend/tests/test_isolation.py          新增：集成（临时库 + 测试专用 HTTP 路由）
docs/spec/s2-3-isolation.md              本文件
docs/plan/s2-3-isolation.md              实施计划
backend/tests/db_helpers.py              复用（S2-2 抽出的临时库助手）
DECISIONS.md / PROGRESS.md / HANDOFF.md / AGENTS.md §10.8 锚点
```

## 5. 测试设计

**单元（不连库）**
- `scope_for` 对三角色 × 三模型生成的 SQL 片段符合预期；
- 未登记模型（`TicketTemplate`）→ `false()`（所有角色）；
- `filter_visible` 不改变原有 where（在传入 stmt 已有条件下仍保留）；
- 策略表恰好覆盖 3 张核心表。

**集成（临时库 `study_ocr_iso_<hex>`，真实迁移）**
- submitter A/B：A 的列表只含自己的票据与文件；B 查 A 的详情抛 `PermissionDeniedError`（外部 404）；
  查不存在的 id 抛 `NotFoundError`；
- reviewer：能看到 A、B 的全部票据/文件；对报销单**只**能看到 `submitted/approved/rejected/archived`，
  看不到 `draft/pending_sign/signed/encrypted`；
- admin：三类业务数据全部 `false()`（有数据也 404）；
- 列表与详情一致性：对每个角色，列表里的 id 集合与"逐个详情是否 404"完全一致；
- HTTP 级（测试专用路由，走真实 `create_app()` 与异常处理器）：
  `B GET /_test/invoices/{A的id}` → **404 + code 40400**；reviewer 200；admin 404；
  `B GET /_test/invoices` 不含 A 的数据；未登录 401；
- `invoice_files` 同样有详情/列表用例（审核要看原图，submitter 互不可见）。

## 6. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest tests/test_repository.py -p no:warnings         # 单元（默认跑）
../.venv/Scripts/python.exe -m pytest -m integration tests/test_isolation.py -p no:warnings   # 集成（需 Docker）
../.venv/Scripts/python.exe -m pytest -p no:warnings                                  # 全量单元
../.venv/Scripts/python.exe -m pytest -m integration -p no:warnings                   # 全量集成
```

通过标准：`test_isolation.py` 全绿；全量 288+ 单元与 51+ 集成不回归；
`B 查 A 的票据` 在 HTTP 层是 404/40400（不是 403，也不是 200）。

**实测（2026-11-19）**：`pytest` → **297 passed**（新增 `test_repository.py` 9 条）；
`pytest -m integration` → **74 passed**（新增 `test_isolation.py` 23 条，含 8 个报销单状态参数化、
四角色列表/详情一致性、HTTP 404/40400 与未登录 401）；`alembic check` 仍零差异（无库结构变更）。

## 7. 与任务卡的偏差（已知并说明）

1. **列名**：任务卡写 `owner_id == 自己`；实际 SCHEMA 是 `invoices/invoice_files.uploader_id`、
   `report_forms.applicant_id`。按 SCHEMA 执行（契约优先），策略表里逐模型声明。
2. **"手工换 id 访问他人单据得 404"的时点**：S2-3 早于 S2-5（票据 CRUD），
   生产环境此时还没有 `/invoices/{id}` 接口。本卡的验收用**测试专用路由**证明完整 HTTP 语义
   （`create_app()` + 真实异常处理器 + `get_current_user`），生产接口在 S2-5 接同一个函数，
   届时再做一次手工换 id 验收。**不提前造一个半成品接口**（列表分页契约属 S2-4）。
3. `report_forms` 的 reviewer 可见状态集合取 `submitted/approved/rejected/archived`；
   若 S5 需要调整（例如驳回后隐藏），改 `REVIEWER_REPORT_STATUSES` 一处即可。

## 8. 决策 D-041（任务卡要求记录）

**为什么用应用层过滤而不是 PostgreSQL RLS**（详细版写进 `DECISIONS.md`）：
- **连接池 + `SET LOCAL` 的坑**：RLS 常用"每请求 `SET LOCAL app.current_user_id`"实现；
  连接池复用连接时，若事务边界与 `SET LOCAL` 作用域不一致（例如用 `SET` 而非 `SET LOCAL`、
  或在事务外设置），会**串用上一个人的身份**——这是比"忘了加 where"更隐蔽的越权。
  本项目还要跑 ARQ worker、脚本、Alembic，它们没有请求上下文，RLS 需要额外的 `BYPASSRLS` 角色与连接串，
  运维复杂度换不来本课题的收益。
- **策略复杂度**：本项目的可见性不是"按人一刀切"：reviewer 对报销单只看到 `submitted` 之后，
  admin 对业务表一律 404。RLS 要写多条 policy + 角色切换；应用层就是一张表 + 一个函数。
- **可测试性**：一个纯函数可以在 pytest 里对 SQL 与结果同时断言；RLS 的错误往往只在真实连接池下复现。
- **代价**：所有查询必须走 `repository.py`（纪律 + grep + 测试兜底），DBA 直连仍可绕过——
  与"库内明文业务字段"的边界一致（SCHEMA §8 已说明），论文里如实写。
- **定位**：RLS 列为"可加固项/展望"，不否定它，只是本课题工期与复杂度下不划算（与 SCHEMA 定稿 #17 一致；
  D-016 实际只写了会话形状，本卡补上它引用却缺失的论证）。

## 9. 风险与回滚

| 风险 | 处理 |
|---|---|
| 新接口忘记调用过滤函数 | deny-by-default 只保护未登记模型；已登记模型仍可能被绕过 → 代码评审 grep + 后续每个读接口的单测必须覆盖"他人数据 200/404" |
| `PermissionDeniedError` 被误当成 403 | 全局处理器已把它转 404；测试直接断言 HTTP 状态与 code |
| reviewer 状态集合写错 | 集中在 `REVIEWER_REPORT_STATUSES` 一处，测试逐状态参数化 |
| 集成测试污染开发库 | 临时库 `study_ocr_iso_<hex>`，`finally` 删除 |

## 10. 收口

- `PROGRESS.md` 勾 S2-3 + 实测；`DECISIONS.md` D-041；
- `AGENTS.md` §10.8 增加"行级数据隔离"锚点（`services/repository.py` + `tests/test_isolation.py`）；
- `HANDOFF.md` 下一步 S2-4；提交 `feat(s2-3): ...`；**push 由用户执行**。