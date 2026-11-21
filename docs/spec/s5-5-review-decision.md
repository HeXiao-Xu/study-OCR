# S5-5 审核决策与驳回 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 8 章 S5-5。
> 契约：`docs/API.md` §5 `POST /api/v1/finance/reports/{id}/review` + SCHEMA §5.5（`review_logs`）+ §9.18。
> 目标：审批**一次成功、不能重放、必须发生在验签之后**；驳回留痕并允许申请人修改重提。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S5-5 行。

## 1. 范围

本卡做 `approve` / `reject` 两个决策 + `review_logs` + 审计 `review` + 前端按钮。
**不做**归档（S5-6）、驳回后重提的信封复用（见 §6 限制）。

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/services/review_service.py` | 决策服务：闸门、行锁、`review_logs`、状态迁移、审计、消费 verify_token | 新 |
| `backend/app/schemas/finance.py` | `ReviewRequest`（条件必填校验）/ `ReviewData`；`FinanceDetailData.report_version` | 改 |
| `backend/app/services/finance_service.py` | 详情补 `report_version`（乐观锁用） | 改 |
| `backend/app/api/v1/finance.py` | `POST /reports/{report_id}/review` | 改 |
| `backend/tests/test_decision.py` | 决策/驳回/凭据/并发/权限（集成） | 新 |
| `web/src/{types,api}/finance.ts`、`views/FinanceList.vue` | 抽屉"通过 / 驳回" | 改 |

**不动**：迁移（本卡**无 0009**；`review_logs` 0001 已建）、0001~0008、密码层、S5-4 验签/解密语义。

## 2. 与任务卡/SCHEMA 的口径差异（以契约为准）

| # | 任务卡/SCHEMA 说法 | 契约/现实 | 本卡执行 |
|---|---|---|---|
| E1 | 路径 `POST /finance/{id}/decision` | API.md §5：`POST /finance/reports/{id}/review` | 按契约 |
| E2 | "同一单只能决策一次（唯一约束）" | SCHEMA §5.5 明确**不加唯一约束**（驳回重提会产生多条审核记录） | **按"同一轮只能决策一次"实现**：单据行 `FOR UPDATE` + `status='submitted'` 闸门；并发第二个请求看到已决策 → 409。`review_logs` 保持追加写 |
| E3 | 未带/无效 verify_token 的 approve | §10 表提到 42202（高风险确认），但 §5 review 错误明确 `409/40900` 未验签 | 按 §5：**409**（`data.reason_code=verify_token_invalid`）；仅"reject 缺 comment"走 422 |
| E4 | "reject 后旧签名置 void" | 契约备注原文 | 决策时立即把 active 签名置 `void`（证据保留，不删除）；`approve` 保持 active |
| E5 | `review_logs.decrypt_ok` | SCHEMA §5.5："信封拆封是否成功" | 以验签结论为准：`verify_result='ok'` 说明 verify 已成功拆封全部信封并解密 → `decrypt_ok=true`；否则 false（本卡不额外要求 decrypt 过） |

## 3. 决策闸门（顺序固定）

```
可见性/reviewer → 单据行 FOR UPDATE → status='submitted' → version 乐观锁
→ active 签名存在 → （approve）verify_token 有效 且 signature.verify_result='ok'
→ 写 review_logs → 状态迁移（approved / rejected）→ 审计 review → commit → 消费 verify_token
```

- **verify_token**：Redis `finance:verify:{report_id}`（S5-4 签发，30min）；approve 时逐字比对，
  不存在/不匹配/过期 → `409/40900` + `reason_code=verify_token_invalid`；
  **commit 后删除**（一次性），避免"验一次批十单"；
- **approve 额外硬校验**：active 签名 `verify_result='ok'`（token 有效但签名被改过也拒绝）；
- **reject**：`comment` 必填 1–500（Pydantic，缺 → `422/42200`）；不要求 verify_token；
  成功后 active 签名置 `void`（申请人可重新签名，旧签名记录保留供审计）；
- **重复决策**：状态已是 `approved/rejected/archived` → 409；并发由行锁串行化（第二个看到新状态）；
- **version 不符** → 409；单据不存在/非 reviewer 可见 → 404；角色不符 → 403。
- 事务：`review_logs`、状态、审计同一事务；Redis 操作在 commit 之后（失败只告警，不回滚已决策）。

## 4. `review_logs` 与审计

- `ReviewLog(form_id, reviewer_id, decision, comment, verify_result=签名当前结论, decrypt_ok=结论=='ok')`；
- 审计 `action=review`，`target_type=form`，`detail={"report_no", "decision", "verify_result", "review_id"}`；
  **不写 comment 正文**（只记长度 `comment_len`），避免审计泄漏审核意见原文；
- `reviewed_at` 取 `review_logs.created_at`（服务端时间）。

## 5. 前端

- 财务抽屉底部：`通过`（**必须先在本会话验签成功**，否则禁用；提示"请先验签"）与 `驳回`（弹窗必填意见）；
- 请求带 `version=detail.report_version`、`decision`、`comment?`、`verify_token?`；
- 成功后：提示、关闭抽屉、刷新列表（已决策单不再出现在"待审核"）；
- 失败：409 提示具体原因（凭据失效/版本冲突/状态不允许），不自动重试。

## 6. 测试（`backend/tests/test_decision.py`，`-m integration`）

复用 `test_submit`/`test_verify` 装置（SM 身份、FakeStorage、内存凭据）。

| # | 用例 | 断言 |
|---|---|---|
| 1 | approve 成功 | 200 `status=approved`；`review_logs` 1 行（decision/verify_result/decrypt_ok）；审计 `review`；verify_token 被删除；待审列表不再含该单 |
| 2 | 未验签直接 approve | 409 `reason_code=verify_token_invalid`；状态仍 submitted；无 review_logs |
| 3 | 错误/过期 token | 409；同上 |
| 4 | 同一 token 第二次 approve | 409（状态已变）；review_logs 仍 1 行 |
| 5 | reject 缺 comment | 422/42200；状态不变 |
| 6 | reject 成功 | 200 `status=rejected`；comment 落库；active 签名变 `void`；审计 `review` |
| 7 | 重复决策 | approve 后再 reject/approve → 409 |
| 8 | version 不符 | 409；状态不变 |
| 9 | 权限 | submitter/admin 403；未知单 404 |
| 10 | 并发 approve | 两线程同一 token → 恰好 1 成功 1 冲突；review_logs 1 行 |
| 11 | token 串单 | A 单 token 用于 B 单 → 409 |
| 12 | approve 后详情 | `GET /finance/{id}` 200、`status=approved`、`verify_status=ok` |

## 7. 验收

1. `pytest tests/test_decision.py -m integration` 全绿；全量单测/集成不回归；`alembic check` 零差异；
2. `npm run typecheck && npm run build` 通过；UI 上"通过/驳回"可用；
3. 真机冒烟：提交→验签→approve（状态/日志/审计/凭据消费）；另一单 reject（comment/void）；清理；
4. 文档：API.md（review 实现口径 + `report_version`）、PROGRESS、DECISIONS **D-088**、HANDOFF（下一步 S5-6）、AGENTS 锚点。

## 8. 已知限制

- **驳回后重提仍受 S5-2 的信封唯一约束限制**（#109）：申请人可以重新签名（rejected→pending_sign→signed），
  但 `submit` 会因 `uq_envelopes_form/file` 报 409。本卡验收不含重提；解决办法（信封版本列/解密重封）留到
  S5-6 或补充卡，论文"不足"如实写；
- "同一单只能决策一次"是**同一轮**语义：驳回后若将来支持重提，同一单会有多条 `review_logs`（SCHEMA 明确不加唯一）；
- `decrypt_ok` 以验签结论为准，不区分"审阅者是否真的看了预览"；
- 决策后预览明文对象不主动删除（随 30min TTL 清理）；
- verify_token 一次性消费在 commit 后执行，Redis 删除失败的极端窗口里 token 仍在，但状态已非 submitted，
  重放会被状态闸门拒绝（不会产生第二次决策）。