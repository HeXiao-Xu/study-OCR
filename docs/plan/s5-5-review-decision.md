# S5-5 审核决策与驳回 — 实现计划（plan）

> 对应 spec：[`docs/spec/s5-5-review-decision.md`](../spec/s5-5-review-decision.md)｜
> 任务卡：实施计划第 8 章 S5-5｜状态：**已实现（2026-11-21）**，Task 0–7 全部执行并验证
> 只动 `backend/app/{services,schemas,api/v1}`、`backend/tests/test_decision.py`、
> `web/src/{types,api,views}` 与文档；**无迁移**（`review_logs` 0001 已建）。

## Global Constraints

- approve 必须先有**有效且未消费**的 verify_token，且 active 签名 `verify_result='ok'`；
- 同一轮只能决策一次：单据行 `FOR UPDATE` + `status='submitted'`（SCHEMA 不允许给 review_logs 加唯一约束）；
- reject 必填 comment（1–500），成功后 active 签名置 `void`（记录保留）；
- review_logs 与状态、审计同一事务；审计不写 comment 正文；
- 仅 reviewer；admin/submitter 403；未知单 404；version 不符 409。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/services/review_service.py` | `decide_report` / 闸门 / 行锁 / review_logs / 状态 / 审计 / 消费 token | 新 |
| `backend/app/schemas/finance.py` | `ReviewRequest`（条件必填）、`ReviewData`；`FinanceDetailData.report_version` | 改 |
| `backend/app/services/finance_service.py` | 详情补 `report_version`（乐观锁） | 改 |
| `backend/app/api/v1/finance.py` | `POST /reports/{report_id}/review` | 改 |
| `backend/tests/test_decision.py` | 12 组集成 | 新 |
| `web/src/{types,api}/finance.ts`、`views/FinanceList.vue` | 通过/驳回 | 改 |

---

### Task 0: 前提

- [x] `review_logs` 列齐全（form_id/reviewer_id/decision/comment/verify_result/decrypt_ok/created_at），无唯一约束（SCHEMA §5.5）；
- [x] S5-4 的 verify_token 在 `CredentialStore`（`verify_key(report_id)`），可用 get/delete；
- [x] `report_service.transition` 支持 `submitted → approved|rejected`（S5-1 已定义）；
- [x] `ReportDecision` 枚举 `approve/reject`、`VerifyResult` 均在 enums 中。

### Task 1: 契约与 schema

- [x] API.md 的 review 备注补实现口径（同一轮、token 消费、decrypt_ok、reject 置 void）；
- [x] `ReviewRequest`：`decision: Literal["approve","reject"]`、`comment`（≤500）、`version`（≥0）、`verify_token`；
      `model_validator`：reject 必须有非空 comment；approve 必须带 verify_token；
- [x] `ReviewData`：`{report_id, report_no, status, decision, reviewed_at}`；
- [x] `FinanceDetailData.report_version`（前端乐观锁用；API.md 同步）。

### Task 2: `services/review_service.py`

- [x] `decide_report(db, user, report_id, payload, *, settings, store) -> ReviewData`：
      可见性 → 行锁 `FOR UPDATE` + `populate_existing` → status/version → active 签名 →
      approve 时校验 token + `verify_result='ok'` → `ReviewLog` → `transition(bump_version=False)` → `version+1` →
      reject 时签名置 `void` → 审计 `review` → commit → 删除 verify_token（失败告警）→ 返回；
- [x] 错误：状态/版本/token → 409（token 带 `reason_code=verify_token_invalid`）；无签名 → 409；未知单 404。

### Task 3: 路由

- [x] `POST /finance/reports/{report_id}/review`，reviewer 依赖，返回 `Envelope[ReviewData]`。

### Task 4: 测试 `test_decision.py`

- [x] 复用 test_submit/test_verify 装置；spec §6 的 12 组：
      approve happy（日志/审计/token 消费/列表移除）、未验签 409、错/过期 token 409、同 token 再用 409、
      reject 缺 comment 422、reject 成功（void 签名）、重复决策 409、version 409、权限、并发、
      跨单 token 409、approve 后详情。

### Task 5: 前端

- [x] `types/finance.ts`：`ReviewData`、`FinanceDetail.report_version`；
- [x] `api/finance.ts`：`reviewReport(reportId, payload)`；
- [x] `FinanceList.vue` 抽屉：`通过`（未验签禁用 + 提示）与 `驳回`（弹窗必填意见）；成功后关抽屉刷新列表；
- [x] `npm run typecheck && npm run build`。

### Task 6: 真机验证

- [x] 提交 2 单 → A：verify → approve（状态 approved、review_logs、审计、token 已删、待审列表剩 1）；
      B：reject + comment（状态 rejected、签名 void、review_logs）；清理业务行（审计保留）。

### Task 7: 收口

- [x] API.md、PROGRESS（勾选 + 产出行 + 变更日志 + 遗留 #121~）、DECISIONS **D-088**、HANDOFF（下一步 S5-6）、AGENTS 锚点；
- [x] 提交 `feat(s5-5): 审核决策与驳回（verify_token 一次性消费 + review_logs）`（push 由用户执行）。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 一次验签批多单/多次 | token 按 report 存、approve 后删除；状态闸门二次兜底 |
| 并发重复决策 | 单据行 `FOR UPDATE`；第二个看到新状态 → 409；专门并发用例 |
| 驳回丢证据 | 签名置 `void` 不删除；`review_logs` 追加写 |
| 审核意见泄漏到审计 | 审计只记 `comment_len`，不写正文 |
| 前端未验签就点通过 | 按钮禁用 + 后端 409 双重 |
| 驳回后重提撞信封唯一约束 | 已知限制 #109，spec §8 写明（不在本卡验收） |