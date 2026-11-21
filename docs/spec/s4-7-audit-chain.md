# S4-7 审计日志哈希链 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-7（★关键）。
> **本卡是阶段 4 唯一跨出 `crypto/` 的卡**：任务卡明确要求 `services/audit.py` +
> `GET /api/v1/admin/audit*` + `scripts/verify_chain.py`，按任务卡执行（S4-8 回到 `crypto/`）。
> 契约：`docs/API.md` §7（`GET /admin/audit`、`GET /admin/audit/verify-chain`）、
> `docs/SCHEMA.md` §6.1（`audit_logs` + D-015 并发定稿）。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S4-7 行。

## 1. 目标

追加写审计行，每行用 **HMAC-SM3 链**覆盖上一行：改任何一条都能被检出，并给出**首个断点**。

```
entry_hash = HMAC-SM3(audit_key, canonical_json({prev_hash, ts, actor_id, actor_role,
                                                 action, target_type, target_id, payload_sha256}))
payload_sha256 = SM3(canonical_json(detail_json or {}))
创世 prev_hash = 64 个 "0"
```

写入串行：同一事务里先 `pg_advisory_xact_lock(固定常量)`，再取链尾（D-015）。

## 2. 范围与取舍

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| Q1 | 哈希覆盖哪些字段 | **SCHEMA 公式 + `actor_role` + `target_type`**（超集） | SCHEMA §6.1 的公式写的是 `prev_hash‖ts‖actor_id‖action‖target_id‖payload_sha256`；但"改一行必被发现"是它的目标——不绑 `actor_role/target_type` 时改这两列无法检出。实现把所有审计字段绑进 canonical 载荷，验证方同一实现；论文公式按实现写并说明与 SCHEMA 文字的差异（D-081） |
| Q2 | `ts` 的形态 | 写入时由 Python 显式给 `created_at`（UTC），哈希里用 `ISO-8601 + 微秒 + +00:00` | 用 DB `now()` 会因时钟/精度差异导致"写完自己都验不过"；SCHEMA 允许显式赋值 |
| Q3 | `payload_sha256` 存哪 | **不存列**（SCHEMA `audit_logs` 没有该列）；验证时从 `detail_json` 重算 | 少一列即少一处可篡改面；detail 被改 → payload_sha256 变 → entry_hash 对不上 |
| Q4 | 审计 HMAC key | `Settings.audit_hmac_key`（必填、≥32 字符），进 `.env.example` | 与 `JWT_SECRET` 同口径，缺失启动即 `ConfigError`；key 不进日志/接口 |
| Q5 | 并发 | 写事务内 `pg_advisory_xact_lock(8071701)` 后取 `MAX(id)` 的 `entry_hash` | D-015 定稿：锁最后一行对空表不阻塞，仍会分叉 |
| Q6 | 只增不改 | `append_audit` 只 `add + flush`，由调用方事务提交；**不提供 UPDATE/DELETE** | SCHEMA 关键不变式 |
| Q7 | 校验失败的形态 | `ChainVerification{ok, checked_count, broken_at(1 起), broken_id, reason}`；reason ∈ `prev_hash_mismatch` / `entry_hash_mismatch` / `payload_invalid` | API.md 出参字段一致；`broken_at` 用行序号、`broken_id` 用主键 |
| Q8 | 业务动作的审计行 | **本卡不接入**业务服务（登录/登出/越权/字段确认/重读等）；只提供 `append_audit` 与链校验 | 每条审计行必须与业务变更同事务，接入要改 6+ 个 service，超出本卡"链 + 校验 + API + 脚本"的交付面；**随 S5 各接口接入**（遗留 #21/#27/#54/#61 重新指向 S5，见 D-081） |
| Q9 | 缺的 `audit_action` 枚举值 | 本卡不加（`template_create/report_create/issue_cert` 仍未在 SCHEMA） | 没有写入方就不需要枚举；S5 写这些动作时按 D-040 流程补 SCHEMA + 迁移（遗留 #20/#37 指向 S5） |
| Q10 | `GET /admin/audit` 列表 | 实现（API.md §7 标注 S4-7）；admin 专用、分页/筛选、`detail_json` 原样返回（写入方已脱敏） | 契约要求；不提供导出（D-013） |

## 3. `services/audit.py` API

```python
GENESIS_HASH = "0" * 64
ADVISORY_LOCK_KEY = 8071701          # 固定常量；同一库内所有 writer 共用

@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    checked_count: int
    broken_at: int | None = None
    broken_id: int | None = None
    reason: str | None = None

def payload_sha256(detail: Mapping[str, Any] | None) -> str       # SM3(canonical(detail or {}))
def chain_hash(*, prev_hash, created_at, actor_id, actor_role,
               action, target_type, target_id, payload_sha256, key: bytes | str) -> str
def append_audit(db, *, action, trace_id, actor_id=None, actor_role=None,
                 target_type=None, target_id=None, detail=None, now=None, key=None) -> AuditLog
def verify_chain(db, *, key=None) -> ChainVerification
```

要点：

- 入参 `action/target_type/actor_role` 用 `models/enums.py` 的枚举校验，非法直接 `ValueError`（DB CHECK 是第二道闸）；
- `detail` 必须是 `dict | None`，且能通过 `canonical_json`（**拒绝 float**——金额类明细先格式化成字符串）；
- `append_audit` 不 commit；调用方与业务变更同一事务提交；
- `verify_chain` 按 `id` 升序流式扫描（`yield_per`），只读、不修改；
- 任何异常都把 `broken_at/broken_id/reason` 填好返回，**不抛给调用方**（`verify-chain` 要 200 而非 500 才好展示）。

## 4. 接口（API.md §7）

- `GET /api/v1/admin/audit`：admin；筛选 `actor_id/action/target_type/target_id/trace_id/from/to`；
  分页/排序 `created_at|id`；返回 `PageResult[AuditLogOut]`；非 admin `403/40301`；非法筛选 `422/42200`。
- `GET /api/v1/admin/audit/verify-chain`：admin；返回
  `{ok, checked_count, broken_at, broken_id, reason}`。
- 新路由在 `main.py` 注册；`test_api_docs` 自动核对 OpenAPI ↔ API.md。

## 5. 脚本与定时任务

- `scripts/verify_chain.py`：读 `backend/.env`（Settings）→ `verify_chain` → 打印 JSON；
  `ok=false` 时退出码 1（便于 CI/运维）。**不打印审计 key**。
- `anchor_audit_task`（S2-12 的占位）替换为**真实每日校验**：跑 `verify_chain` 并记录结果
  （不断链 → info；断链 → error + reason）；调度骨架与 Redis 锁不动。**不发明"锚定表"**（SCHEMA 没有）。

## 6. 测试

`test_audit.py`（默认跑，纯函数）：

| 用例 | 断言 |
|---|---|
| `payload_sha256` 与 canonical 一致 | 同 detail 同值；detail=None 等价 `{}` |
| detail 含 float/非 str 键 | `CanonicalJSONError` |
| `chain_hash` 确定性 | 同输入同值；改任一字段（prev/ts/actor/role/action/target_type/target_id/payload）值都变 |
| 创世常量 | `GENESIS_HASH == "0"*64` |
| 枚举校验 | 非法 action/target_type/actor_role → `ValueError` |

`test_audit_chain.py`（`@pytest.mark.integration`，临时库 + Alembic）：

| 用例 | 断言 |
|---|---|
| 追加 3 条 | `verify_chain.ok=True`、`checked_count=3`、`prev_hash` 链正确、创世行 prev=0×64 |
| 改中间一条 `detail_json`（SQL UPDATE） | `ok=False`、`broken_at=2`、`broken_id=第二条 id`、`reason="entry_hash_mismatch"` |
| 改中间一条 `entry_hash` | `ok=False`、断点是该行 |
| 删中间一条 | `ok=False`（下一行 prev 对不上），`reason="prev_hash_mismatch"` |
| 并发 50 条（多线程各自事务） | 全部成功、`checked_count=50`、`verify ok`、无两条共用同一 prev |
| 事务回滚 | 回滚后链不变（只读校验仍 ok） |
| HTTP | admin 列表/校验 200；reviewer/submitter 403/40301；筛选/分页；非法 `action` 422/42200 |

`scripts/verify_chain.py`：用真实库跑一次（有数据时）并检查退出码。

## 7. 验收

1. `pytest backend/tests/test_crypto -q` 不回归（220）+ 新增审计单测全绿；
2. `pytest -m integration`：审计链集成用例全绿（含并发 50）；
3. 篡改中间一条 payload → `verify-chain` 报正确 `broken_at/broken_id`；
4. `scripts/verify_chain.py` 在正常库上输出 `ok=true`、退出码 0；
5. 全量 `pytest` 无回归；`alembic check` 零差异（本卡无迁移）；
6. 文档收口：PROGRESS / DECISIONS（D-081）/ AGENTS 锚点 / HANDOFF。

## 8. 已知限制（论文"安全边界"）

- 哈希链防"改了不被发现"，**不防"被读"**；同时持有审计 key 的人可整体重写（不是区块链）；
- 业务动作的审计行尚未接入（随 S5）；`template_create/report_create/issue_cert` 枚举仍未加；
- 全量校验是 O(n) 扫描（5 万行以内可接受；论文如实写）；
- 每日锚定只是"再验证一次 + 记日志"，没有外部锚（时间戳服务/公证），删掉整条链仍无法发现。`