# S5-1 报销单创建与明细 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 8 章 S5-1（阶段 5 第一张）。
> 契约：`docs/API.md` §4（`POST /reports`、`POST /reports/{id}/items`、`GET /reports/{id}`、`GET /reports/mine`）
> + `docs/SCHEMA.md` §5.1 / §5.2（表在 0001 已建，本卡不改列）。
> 目标：单据与明细可建、金额自洽、只能引用已确认票据；为 S5-2 的签名/封装准备**确定的数据形态**。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S5-1 行。

## 1. 范围

本卡只做 `draft` 这一段：建单、加明细、看详情、看我的列表。
**不做**：签名（S5-2）、提交/信封（S5-2）、财务列表/验签/审核（S5-3~S5-5）、归档（S5-6）、
明细删除/修改接口（契约未定义）、`PATCH /reports/{id}`（API.md §12.1 只提到语义，没有接口条目，留到 S5-5 驳回重提时一起做）。

产出（按 HANDOFF §7 预期）：

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/schemas/report.py` | 出入参：`ReportOut` / `ReportItemOut` / `ReportCreateRequest` / `ReportItemCreateRequest` / `ReportDetailData` / `ReportListQuery` / `SignatureOut` / `EnvelopeOut` | 新 |
| `backend/app/services/report_service.py` | 单号、状态机、建单、加明细、详情、我的列表（一个用例一个事务） | 新 |
| `backend/app/api/v1/reports.py` | 四条路由，只做参数校验与调用 | 新 |
| `backend/app/models/enums.py` | `AuditAction.REPORT_CREATE` | 改 |
| `backend/alembic/versions/0008_report_number_and_audit_action.py` | `report_no_seq` 序列 + `ck_audit_logs_action` 放开 `report_create` | 新 |
| `backend/app/services/pagination.py` | `apply_sort`/`paginate` 增加可选 `nulls_last`（默认 False，行为不变） | 改 |
| `backend/tests/test_reports.py` | 集成用例（建单/明细/金额/权限/并发/回滚/状态机） | 新 |
| `docs/{API,SCHEMA,PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` | 契约与进度同步 | 改 |

**不改**：0001~0007 迁移、`report_forms` / `report_items` 列、密码层参数、16 张表清单。

## 2. 与任务卡口述的偏差（以契约为准，AGENTS §9 / HANDOFF §7.1）

| # | 任务卡口述 | 契约（API.md / SCHEMA） | 本卡执行 |
|---|---|---|---|
| E1 | 建单入参含 `department` | SCHEMA 无该列；API.md：`department` 取 `users.dept`，且**不接受客户端传值** | 建单只收 `title`/`reason`；`ReportOut.department` 是**派生字段**（申请人 `users.dept`），不落库 |
| E2 | 建单入参含 `applicant_id`、`total_amount` | 申请人 = 当前用户；合计由明细汇总，"不信客户端" | 不收；建单后 `total_amount="0.00"`、`item_count=0` |
| E3 | 明细后"校验合计 == total_amount（容差 0.01，不符 422）" | 合计由服务端**重算**，没有"客户端报合计"这回事 | 不做合计比对；`422/42201` 用于 `amount > 票据 total_amount`（`data.expected`），这才是契约里的"金额不符" |
| E4 | `RPT-YYYYMM-序号` | API.md 示例 `RPT-202611-0007`，4 位补零；HANDOFF §7：PG 序列，并发不重号 | 全局序列 `report_no_seq`（月份取 `Asia/Shanghai` 业务月）；序号**全局递增**、不按月重置（唯一性由序列 + `uq_report_forms_report_no` 双保险；>9999 自然变 5 位，不截断） |
| E5 | "同一 invoice 不能被两张有效单据引用（唯一约束）" | SCHEMA 只有 `UNIQUE(form_id, invoice_id)`，**没有跨单唯一约束**；API.md 备注为 TODO | 见 §4：应用层校验 + 行锁串行化，**不新增列/约束**（SCHEMA 是唯一依据，不能为它发明冗余列） |

## 3. 单号与事务

### 3.1 单号

```
seq = SELECT nextval('report_no_seq')
report_no = "RPT-{YYYYMM}-{seq:04d}"        # YYYYMM 按 Asia/Shanghai
```

- 序列**非事务性**：回滚不会退号（可接受：单号只求唯一、不求连续）；
- 为什么不用 `count()+1`：并发下必重号（任务卡坑）；
- 为什么不用"按月序列"：需要运行期 DDL 建序列或计数器表（第 17 张表），契约没给；全局序列已满足"不重号"。

### 3.2 事务边界

- `create_report`：插 `report_forms` → `append_audit(action=report_create)` → `commit`；异常 `rollback`；
- `add_item`：锁票据行 → 校验 → 插 `report_items` → 改 `invoices.status='in_report'` → 重算合计 → `commit`；
  任一步失败整体回滚（票据状态与明细同生共死，SCHEMA §5.2 跨表不变式）；
- 审计行不单独提交：与业务变更同一事务（D-081 的纪律）。

## 4. "一张票据只能进一张有效单据"的兜底

SCHEMA 没有跨表唯一约束，且部分唯一索引无法表达"join 另一张表的状态"。三条路：
触发器（库内 PL/pgSQL，难测、难审计）、冗余列 + 部分唯一索引（要改 SCHEMA）、应用层 + 行锁。

**拍板：应用层 + 行锁串行化（D-084）**：

0. 先锁**单据行**（`report_forms ... FOR UPDATE`），再锁票据行——固定顺序防死锁；
   同一单据的并发加明细因此排队，`total_amount/item_count` 的重算不会读到别人未提交的中间态而"丢一条"；
1. `SELECT ... FROM invoices WHERE id = :id FOR UPDATE` 锁住票据行——同一票据的并发添加在这里排队；
   拿到锁后用 `populate_existing` 读最新已提交的行判断：
   - `status='confirmed'` → 可加；
   - `status='in_report'` → `409/40901`（"已被其他有效单据引用"，比 40900 更准确）；
   - 其它（`draft/archived/void`）→ `409/40900`；
3. 再查跨单引用：`report_items JOIN report_forms ON ... WHERE invoice_id=:id AND form_id<>:form AND status <> 'rejected'` → `409/40901`；
4. 同单重复：先查 → `409/40901`；并发撞 `UNIQUE(form_id, invoice_id)` 时捕获 `IntegrityError` 同样转 `409/40901`。

为什么这样就够：两步写（A 加票 X 进单 1、B 加票 X 进单 2）必然在同一票据行上串行，
后到者看到 `status='in_report'` 直接 40901；不存在"先查后插都通过"的窗口。
`rejected` 不计入"有效单据"：驳回单要能保留明细供修改重提（API.md §12.1），
且被驳回的单据其票据状态仍为 `in_report`，本来也进不了新单（回到 `confirmed` 属 S5-5 的职责）。

## 5. 状态机

集中在 `report_service.transition(db, form, target, actor)`，`ALLOWED_TRANSITIONS`：

```
draft        → pending_sign
pending_sign → signed
signed       → encrypted
encrypted    → submitted
submitted    → approved | rejected
approved     → archived
rejected     → pending_sign        # 驳回后重签（API.md §12.1）
archived     → （终态）
```

- 目标是同一状态 → 幂等返回，不改 `version`（与 `invoice_service.transition` 一致）；
- 非法迁移 → `409/40900`；目标不在 8 值内 → `422/42200`；
- 只 flush 不 commit（S5-2 要在同一事务里做规范化/签名/封装/推进）；
- S5-1 只写 `draft`，本卡不调用 `transition`，但**先落地并单测**（任务卡明确要求"集中 transition()"）。

## 6. 接口口径（照 API.md §4，不照任务卡口述）

| 接口 | 关键点 |
|---|---|
| `POST /reports` | submitter（reviewer 403、admin 404）；201；`ReportOut{status=draft, total_amount="0.00", items=[]}`；写审计 `report_create` |
| `POST /reports/{id}/items` | 本人、仅 `draft`/`rejected`；201 返回**完整 `ReportOut`**（含 items、重算合计、条数）；票据 `status→in_report` 同事务 |
| `GET /reports/{id}` | 本人 / reviewer（仅 `submitted` 之后）/ admin 404；`{report, signature, envelope, verify_status}`；**绝不返回** `ciphertext_b64`/`wrapped_key_b64`/`ciphertext_storage_key` |
| `GET /reports/mine` | 仅 submitter（reviewer/admin 403）；分页 + `status`/`date_from`/`date_to`/`sort_by`；列表**不含明细**（避免 N+1） |

错误码：`404/40400` 不存在或非本人；`409/40900` 状态不允许/票据未确认；`409/40901` 重复引用；
`422/42200` 参数校验（Pydantic）；`422/42201` 金额超票面（`data.expected`）。

金额：入参/出参都是**两位小数字符串**（`"123.45"`），内部 `Decimal` + `NUMERIC(12,2)`；
入参 `Decimal` 超过两位小数或负值 → 42200；禁止 float 参与任何计算。

## 7. 审计

- `AuditAction.REPORT_CREATE = "report_create"`（SCHEMA §9.19b / enums.py / 迁移 0008 CHECK 三处同步）；
- `append_audit(action="report_create", actor_id=user.id, actor_role="submitter", target_type="form",
  target_id=form.id, detail={"report_no", "status"})`——**只记单号与状态**，不记标题/事由/金额（脱敏纪律）；
- 明细添加**不写审计**：契约只要求建单写；且 8 值 `action` 里没有"加明细"，不为它发明新值
  （提交动作在 S5-2 一次性覆盖整单，D-081 的能力边界写在论文里）；
- 同事务提交：建单失败不会留下审计行，审计 chain 不会因业务回滚而断（D-081）。

## 8. 测试（`backend/tests/test_reports.py`，`-m integration`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 建单 | 201、`report_no` 匹配 `^RPT-\d{6}-\d{4,}$`、`status=draft`、`total_amount="0.00"`、`items=[]` |
| 2 | 建单审计 | `audit_logs` 多一行 `report_create`，`target_type=form`、`target_id=单 id`；`verify_chain` 仍 ok |
| 3 | 参数校验 | `title` 空/超 100、`reason` 空/超 500 → 422/42200 |
| 4 | 角色 | reviewer → 403；admin → 404 |
| 5 | 加 3 条明细 | 201；`total_amount="330.00"`、`item_count=3`；明细金额精确（Decimal 无漂移） |
| 6 | 跨表不变式 | 3 张票据 `status='in_report'`、`version` +1；`GET /reports/{id}` 返回 3 条 |
| 7 | 金额超票面 | 422/42201，`data.expected` 为票面合计 |
| 8 | 负金额 / 三位小数 / 0 元 | 前两者 42200；0 元允许（`0 ≤ amount`） |
| 9 | 非本人票据 | 404/40400（不泄露存在性） |
| 10 | 未确认票据 | 409/40900 |
| 11 | 同单重复 | 409/40901（`UNIQUE(form_id, invoice_id)` 兜底） |
| 12 | 跨单重复 | 先建单 A 加票 X，再建单 B 加票 X → 409/40901；票据仍是 A 的 `in_report` |
| 13 | 单据状态不允许 | 单置 `pending_sign` 后加明细 → 409/40900 |
| 14 | 回滚可复原 | 失败路径后票据 `status='confirmed'`、`report_items` 无新增、单据合计/条数不变 |
| 15 | 并发建单 | 10 线程各自建单 → 10 个不同 `report_no`、库内 10 行、`uq_report_forms_report_no` 未被触发 |
| 16 | 并发加同一票到两单 | 两个线程同时加 → 恰好一个 201、另一个 409/40901；票据只被一单引用 |
| 16b | 并发加不同票到同一单 | 两线程同时加 → 都 201，`item_count=2`、合计等于两条之和（防"丢更新"） |
| 17 | 详情权限 | 他人 submitter 404；reviewer 对 draft 404、对 `submitted` 200；admin 404 |
| 18 | 我的列表 | 只含本人；`status`/日期筛选生效；`sort_by=submitted_at` 空值排最后；非法 `sort_by` → 422 |
| 19 | 非 submitter 访问 `/mine` | reviewer/admin → 403/40301 |
| 20 | 状态机单测 | 合法链逐个通过；`draft→submitted` 409；同状态幂等；`rejected→pending_sign` 允许；非法值 422 |

并发用例用**独立 Session + 线程**直接调 service（TestClient 多线程会互相干扰依赖覆盖），
主线程用 `ThreadPoolExecutor` 收集结果。

## 9. 验收

1. `pytest tests/test_reports.py -m integration -q` 全绿；
2. `alembic upgrade head`（head=0008）、`alembic check` 零差异；
3. 全量单测（默认跳过集成）与全量集成不回归；
4. 文档同步：API.md（S5-1 相关实现注释）、SCHEMA §9.19b（`report_create`）、PROGRESS、DECISIONS **D-084**、
   HANDOFF（下一步 S5-2）、AGENTS §10.8 锚点（报销单 service）。

## 10. 已知限制（论文"不足"可写）

- 单号序列全局递增、**不按月重置**（示例语义 vs 实现口径的差异）；
- "一票一单"靠应用层 + 行锁，不是数据库硬约束：**绕过 service 直写库**仍可能造成重复引用；
- 明细只增不删不改（契约未定义接口），驳回重提的明细编辑走 S5-5；
- 建单/加明细没有速率限制，创建者可无限建空单（S5-7 联调时看是否要清理策略）。