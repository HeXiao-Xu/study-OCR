# S5-3 财务端待审列表 — 实现计划（plan）

> 对应 spec：[`docs/spec/s5-3-finance-list.md`](../spec/s5-3-finance-list.md)｜
> 任务卡：实施计划第 8 章 S5-3｜状态：**已实现（2026-11-21）**，Task 0–8 全部执行并验证
> 只动 `backend/app/{schemas,services,api/v1,utils}`、`backend/tests/test_finance.py`、
> `web/src/{types,api,views,router,utils}` 与文档；**无迁移**（SCHEMA 不新增列）。

## Global Constraints

- 仅 `reviewer`；`submitter` / `admin` → `403/40301`（财务区"不得代审"）；
- 列表**不返回** `ciphertext_b64` / `wrapped_key_b64` / `ciphertext_storage_key` / `storage_path`；
- `verify_status` 由 `signatures` 派生，**不在请求里做密码运算**；
- 分页复用 S2-4（同一 `Select` 求 total、`id asc` 兜底、白名单排序）；
- CSV：UTF-8 BOM + `\r\n` + 流式 `StreamingResponse`，与列表同筛选；
- 业务日换算只在一个地方（`utils/time.py`）。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/utils/time.py` | `business_day_bounds(date_from,date_to)` | 新 |
| `backend/app/schemas/finance.py` | `FinancePendingQuery/Item`、`FinanceAttachmentOut`、`FinanceDetailData` | 新 |
| `backend/app/services/finance_service.py` | `list_pending` / `get_detail` / `iter_pending_csv` | 新 |
| `backend/app/api/v1/finance.py` | 3 条路由（`/pending`、`/pending/export` 先于 `/{report_id}`） | 新 |
| `backend/app/main.py` | 挂载 finance router | 改 |
| `backend/app/services/report_service.py` | `verify_status_of` / `envelope_out` 公开；日期区间改调公共助手 | 改 |
| `backend/app/services/submit_service.py` | 提交验签通过后落 `verified/verify_result/verified_at` | 改 |
| `backend/tests/test_finance.py` | 12 组集成 | 新 |
| `web/src/types/finance.ts` / `api/finance.ts` | 类型与 API 封装 | 新 |
| `web/src/views/FinanceList.vue` | 列表 + 详情抽屉 + CSV 下载 | 新 |
| `web/src/router/index.ts` / `utils/format.ts` | 真实组件 + 验签标签 | 改 |

---

### Task 0: 前提

- [x] `signatures.verified/verify_result/verified_at` 已存在（0001），无需迁移；
- [x] `repository.POLICIES` 已允许 reviewer 看 `submitted/approved/rejected/archived`；
- [x] S5-2 已落单据级/文件级信封与 `payload_sha256`，详情可直接展示元数据；
- [x] 前端 `/finance/pending` 占位路由与"待审核"菜单已存在。

### Task 1: 公共助手 + S5-2 修正

- [x] `utils/time.py::business_day_bounds`：`[from, to]` → UTC 半开区间；`from>to` → `42200`；
      `report_service._date_bounds` 改调它（行为不变）；
- [x] `report_service.verify_status_of(signature)` 公开（`report_service` 详情与 finance 共用）；
      `report_service.envelope_out(db, row)` 公开；
- [x] `submit_service.submit_report`：提交验签通过后写
      `signature.verified=True; verify_result='ok'; verified_at=now; verifier_id=None`
      （SCHEMA §5.3"submitted 之后必须已验证"；系统自动验签 = verifier_id 为空）；
      单测补断言。

### Task 2: `services/finance_service.py`

- [x] `list_pending`: `select(ReportForm).join(User).outerjoin(Signature active)` + `filter_visible`
      + `status='submitted'` + 三态/日期/q 筛选 + `paginate(scalar=False)`；返回 `PageResult[FinancePendingItem]`；
- [x] `get_detail`：`report_service.get_report_detail` 取单据/签名/信封/verify_status →
      再查明细文件 + 文件级信封（一次查询，无 N+1）→ `FinanceDetailData`；
- [x] `iter_pending_csv`：同一 `_pending_stmt`，按 `submitted_at asc` 拉取（上限 10000），
      yield BOM + 表头 + 每行（中文表头、`\r\n`、Asia/Shanghai 时间）。

### Task 3: 路由与 schemas

- [x] `GET /finance/pending`（200，`PageResult[FinancePendingItem]`）；
- [x] `GET /finance/pending/export`（`StreamingResponse`，`text/csv; charset=utf-8`，Content-Disposition）；
- [x] `GET /finance/{report_id}`（200，`FinanceDetailData`）；
- [x] 三条都 `Depends(require_roles("reviewer"))`；`main.py` 挂载。

### Task 4: 测试 `test_finance.py`

- [x] 复用 `test_submit` 的 SM 身份（Docker）+ FakeStorage，构造 5 单（每单 1 票）；
- [x] spec §3 的 12 组：5 条可见、`verify_status=ok`、三态筛选、q、日期、排序、权限、
      详情形状与"无密文"、详情 404、CSV 正确性/权限/筛选一致、**2 条 SQL 断言**。

### Task 5: 前端

- [x] `types/finance.ts`：`VerifyStatus`、`FinancePendingItem/Page`、`FinanceDetail`、`FinanceAttachment`；
- [x] `api/finance.ts`：`listPending`、`getFinanceDetail`、`downloadPendingCsv`（blob + 本地下载）；
- [x] `FinanceList.vue`：筛选/分页走 `route.query`；表格 + 验签 tag；详情抽屉；导出按钮；
- [x] `router`：`/finance/pending` → `FinanceList.vue`；`format.ts` 加 `VERIFY_STATUS_LABEL/TAG`；
- [x] `npm run typecheck && npm run build` 通过。

### Task 6: 真机/API 验证

- [x] 开发库提交 5 单（用真 MinIO + alice 身份）→ `/finance/pending` 5 条、`verify_status=ok`；
- [x] 下载 CSV → 检查 BOM/CRLF/中文表头/行数（Python 读取 + 字节头断言）；
- [x] submitter 访问 → 403；清理这 5 单的业务行与对象（审计行保留）。

### Task 7: 文档收口

- [x] API.md：补 `GET /finance/{id}`、`GET /finance/pending/export` 两条 + S5-3 备注
      （verify_status 派生、CSV 口径）；
- [x] DECISIONS **D-086**（verify_status 无新列 + 提交时系统验签落库的修正 + 两条新契约 + CSV 口径）；
- [x] PROGRESS（勾选 + 产出行 + 变更日志 + 遗留 #113~）；AGENTS §10.8 锚点；
- [x] HANDOFF：下一步 S5-4、库表行数、命令；
- [x] 提交 `feat(s5-3): 财务待审列表 + 详情元数据 + CSV 导出（verify_status 派生）`（push 由用户执行）。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 列表带出密文/对象名 | 出参 schema 里根本没有这些字段；测试断言响应文本不含 |
| 实时验签导致慢 | verify_status 只读 `signatures` 列；SQL 断言整页 2 条 |
| join 造成行放大 | `signatures` 的 active 部分唯一索引保证一单一行 |
| CSV 中文乱码 | BOM + UTF-8 + `\r\n`；测试断言首字节 `EF BB BF` |
| 导出拖死进程 | 上限 10000 行 → 422 提示收窄筛选 |
| 前端与后端字段漂移 | `types/finance.ts` 按 API.md 手写；typecheck 兜底 |