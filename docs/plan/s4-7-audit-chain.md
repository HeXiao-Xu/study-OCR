# S4-7 审计日志哈希链 — 实现计划（plan）

> 对应 spec：[`docs/spec/s4-7-audit-chain.md`](../spec/s4-7-audit-chain.md)｜
> 任务卡：实施计划第 7 章 S4-7｜状态：**已实现（2026-11-21）**，Task 0–5 全部执行并验证
> **本卡跨出 `crypto/`**：动 `services/audit.py`、`api/v1/admin.py`、`core/config.py`、
> `tasks/ocr_task.py`、`scripts/verify_chain.py` 与测试；不改表（`audit_logs` 0001 已建）。

## Global Constraints

- 只 INSERT、不 UPDATE/DELETE；`append_audit` 不 commit（与业务同一事务）；
- 写链前必须 `pg_advisory_xact_lock(8071701)`，再取链尾（D-015）；
- 哈希载荷用 `canonical_json`（拒绝 float/非 str 键）；`ts` 显式 UTC + 微秒；
- 审计 key 进 `Settings.audit_hmac_key`（必填 ≥32 字符），不进日志/接口；
- 本卡**不接入**业务审计行（S5），不加 `audit_action` 枚举（S5 按 D-040 走）。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/core/config.py` + `backend/.env.example` | `AUDIT_HMAC_KEY`（必填 ≥32） | 改 |
| `backend/app/services/audit.py` | `GENESIS_HASH` / `ADVISORY_LOCK_KEY` / `ChainVerification` / `payload_sha256` / `chain_hash` / `append_audit` / `verify_chain` | 新 |
| `backend/app/schemas/audit.py` | `AuditLogOut` / `AuditListQuery` / `AuditVerifyData` | 新 |
| `backend/app/api/v1/admin.py` | `GET /admin/audit`、`GET /admin/audit/verify-chain` | 新 |
| `backend/app/main.py` | 注册 admin 路由 | 改 |
| `backend/app/tasks/ocr_task.py` | `anchor_audit_task` 换成真实校验 | 改 |
| `scripts/verify_chain.py` | 命令行校验（退出码 0/1） | 新 |
| `backend/tests/{test_audit.py,test_audit_chain.py}` | 纯函数 5 组 + 集成（链/篡改/删除/并发 50/HTTP） | 新 |
| `backend/tests/{conftest.py,test_config.py}` | 注入 `AUDIT_HMAC_KEY`、必填清单更新 | 改 |
| `docs/{spec,plan}/s4-7-audit-chain.md`、`docs/{PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` | 收口 | 新/改 |

---

### Task 0: 探针（已执行）

- [x] 契约核对：API.md §7 的两个接口名是 `verify-chain`（不是任务卡的 `/verify`），出参
      `{ok, checked_count, broken_at, broken_id, reason}`；
- [x] SCHEMA `audit_logs` 无 `payload_sha256`/`ts` 列，使用 `created_at`/`detail_json`/多态 `target_*`；
- [x] D-015 并发定稿：`pg_advisory_xact_lock` 固定常量；枚举在 `models/enums.py`。

---

### Task 1: 配置（先红后绿）

- [x] `Settings` 增加 `audit_hmac_key: str`（≥32 字符校验）；`get_settings` 报错信息不变；
- [x] `backend/.env.example` 增加 `AUDIT_HMAC_KEY=`；本地 `backend/.env` 追加随机值（不入库）；
- [x] `conftest.py` 注入测试值；`test_config.py` 的 `_settings` 与必填参数化更新；
- [x] 先写 `test_config` 用例（缺 key 报错）→ 红 → 实现 → 绿。

---

### Task 2: `audit.py` + 纯函数测试（先红后绿）

- [x] 写 `test_audit.py`（5 组）→ 红；
- [x] 实现 `payload_sha256` / `chain_hash` / `append_audit` / `verify_chain`；
- [x] 绿；`tests/test_crypto` 不回归。

---

### Task 3: 接口 + 集成测试

- [x] `schemas/audit.py`、`api/v1/admin.py`、`main.py` 注册；
- [x] `test_audit_chain.py`：临时库 + Alembic；追加/篡改/删除/并发 50/HTTP 403/筛选分页；
- [x] `pytest -m integration` 绿。

---

### Task 4: 脚本 + 定时任务

- [x] `scripts/verify_chain.py`（Settings → `verify_chain` → JSON；断链退出 1）；
- [x] `anchor_audit_task` 真校验（不断链 info / 断链 error）；调度骨架与锁不动；
- [x] `test_scheduler` 不回归（占位断言需更新）。

---

### Task 5: 收口与提交

- [x] 全量 `pytest` + `pytest -m integration` + `alembic check`；
- [x] PROGRESS（勾选/产出/验收/遗留重定向：#21/#27/#54/#61 → S5；#20/#37 → S5）、
      DECISIONS **D-081**（哈希载荷超集、payload 不落列、业务行留 S5、reason 取值）、
      AGENTS §10.8 审计锚点、HANDOFF（下一步 S4-8）；
- [x] 提交 `feat(s4-7): 审计日志哈希链（HMAC-SM3 + 行级锁 + verify-chain）`；**push 由用户做**。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 并发写分叉 | 集成测试 50 线程；锁常量写死；失败即暴露 |
| `ts` 精度导致自验失败 | 写入显式给 `created_at`；集成测试"写完立即 verify"覆盖 |
| 业务服务接入范围膨胀 | 本卡明确不接入；遗留重定向到 S5 |
| 大表全量扫描慢 | `yield_per` 流式；5 万行以内可接受；论文写 O(n) |
| 枚举新增值破坏 DB CHECK | 本卡不加枚举，无需迁移 |