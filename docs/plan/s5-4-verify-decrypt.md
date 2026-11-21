# S5-4 验签与解密 — 实现计划（plan）

> 对应 spec：[`docs/spec/s5-4-verify-decrypt.md`](../spec/s5-4-verify-decrypt.md)｜
> 任务卡：实施计划第 8 章 S5-4（★关键）｜状态：**已实现（2026-11-21）**，Task 0–9 全部执行并验证
> 只动 `backend/app/{services,api/v1,schemas,core,tasks}`、`backend/tests/test_verify.py`、
> `web/src/{types,api,views}` 与文档；**无迁移**（SCHEMA 不新增列）。

## Global Constraints

- 口令**只从环境变量** `FINANCE_KEY_PASSPHRASE`（D-016），不进 HTTP/日志/异常；
- 验证顺序固定七步；失败 409 + `reason_code`/`failed_step`，**不返回明文**；
- 摘要**从库内原始数据重算**（含用 canonical 里的 nonce 重建签名域），不拿存下的摘要直接比；
- `mac_mismatch`/`bad_padding` **不写** `signatures.verify_result`；`digest_mismatch`/`bad_signature`/`cert_invalid` 写；
- 预览明文只进 `previews/` 临时对象（TTL 30min）+ Redis 凭据，**DB 不落明文**；
- 一个用例一个事务：验签结论与审计同事务；解密只读 + 写临时对象，不写业务表。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/services/credential_store.py` | `CredentialStore` 协议 + Redis 实现 + 内存实现（测试） | 新 |
| `backend/app/services/report_crypto.py` | `open_sealed_steps()` 分步解封；`rebuild_canonical_from_db()` | 改 |
| `backend/app/services/verify_service.py` | 七步验签、失败归类、签名结论落库、审计、签发 verify_token | 新 |
| `backend/app/services/preview_service.py` | 解密到临时对象、预览凭据、读取、TTL 清理 | 新 |
| `backend/app/services/storage.py` | `list_objects(prefix)` | 改 |
| `backend/app/schemas/finance.py` | `VerifyStep/VerifyData/DecryptRequest/DecryptData` | 改 |
| `backend/app/api/v1/finance.py` | `verify`/`decrypt`/`preview`/`preview/files` | 改 |
| `backend/app/core/config.py` + `.env.example` | 财务口令、预览 TTL/清理/前缀/锁 | 改 |
| `backend/app/tasks/scheduler.py` | 预览清理 IntervalTrigger + 抢锁 | 改 |
| `backend/tests/test_verify.py` | 12 组集成 | 新 |
| `web/src/{types,api}/finance.ts`、`views/FinanceList.vue` | 抽屉"验签/解密预览/看图" | 改 |

---

### Task 0: 前提

- [x] S5-2 的 `EnvelopeV1`（AAD 可重建）+ 财务私钥（`secrets/keys/finance`）已就位；
- [x] S5-3 的待审/详情与 `verify_status` 派生已就位（失败时列表自动变红）；
- [x] Redis 客户端单例与 `RefreshTokenStore` 的协议模式可复用（测试注入内存实现）；
- [x] Scheduler 的"抢锁 + 成功后锁留 TTL"模式可复用。

### Task 1: 配置 + 凭据存储

- [x] `Settings`：`finance_key_passphrase`、`preview_token_ttl_seconds=1800`、
      `preview_cleanup_interval_seconds=600`、`preview_object_prefix="previews"`、
      `preview_cleanup_lock_key`/`_ttl_seconds`；`.env.example` 同步；
- [x] `credential_store.py`：`CredentialStore(Protocol)` + `RedisCredentialStore`（`setex/get/delete`）+ `InMemoryCredentialStore`（测试；可手动过期）；
      `get_credential_store()`（lru_cache）供路由依赖注入。

### Task 2: 解封分步 + 重建签名域

- [x] `report_crypto.open_sealed_steps(...) -> SealedOpenResult{plaintext, steps}`：
      返回 `unwrap_cek/mac/decrypt` 三段的 ok/失败归类（`EnvelopeOpenError(reason)` 保留）；
- [x] `report_crypto.rebuild_signing_canonical(form, items, stored_canonical)`：
      从 stored canonical 取 `nonce`，用 `build_signing_payload` 重建并 `canonical_json`，返回字节；
      解析失败 → `digest_mismatch` 语义务。

### Task 3: `services/verify_service.py`

- [x] `verify_report(db, user, report_id, *, settings, store) -> VerifyData`：
      reviewer（路由保证）→ `get_visible_or_404` → 七步 → 成功 commit 审计 + 签发 token；
- [x] 失败：写审计 `result=failed`（含 reason/step），按理由更新签名结论（仅签名层），commit 后抛
      `VerificationFailedError(code, reason_code, failed_step)`；
- [x] `VerificationFailedError`：HTTP 409、`code` 由类别决定、`detail={reason_code,failed_step}`。

### Task 4: `services/preview_service.py`

- [x] `decrypt_report(db, user, report_id, verify_token, *, settings, store, storage)`：
      校验 verify_token → 解 form JSON + 各影像 → `previews/{token}/...` → Redis 预览凭据 → `DecryptData`；
- [x] `read_preview(store, storage, token) -> (json_bytes, meta)`；`read_preview_file(...) -> (bytes, mime)`；
      过期/不存在 → `PreviewExpiredError`（410/41001）；
- [x] `cleanup_expired_previews(storage, settings, *, now=None) -> int`：按对象 `LastModified` 删过期；
      `run_preview_cleanup_once(lock/store/settings)` 抢锁包装（scheduler 用）。

### Task 5: 路由与 schemas

- [x] `POST /finance/reports/{report_id}/verify`（无请求体）；
- [x] `POST /finance/reports/{report_id}/decrypt`（`{verify_token}`）；
- [x] `GET /finance/preview/{preview_token}`（JSON）与 `GET /finance/preview/{preview_token}/files/{file_id}`（影像）；
- [x] 全部 `require_roles("reviewer")`；`get_credential_store` 依赖可注入；`main.py` 无需改（router 已挂）。

### Task 6: 清理任务接入调度

- [x] `tasks/scheduler.py`：`IntervalTrigger(seconds=PREVIEW_CLEANUP_INTERVAL_SECONDS)` +
      `run_preview_cleanup_once`（抢 `preview_cleanup_lock_key`，成功后锁留到 TTL）；
- [x] 配置开关沿用 `SCHEDULER_ENABLED`。

### Task 7: 测试 `test_verify.py`

- [x] 复用 `test_submit` 装置；`settings` 用 `get_settings().model_copy(update={"finance_key_passphrase": ...})` + 路由依赖覆盖；
      Redis 用 `InMemoryCredentialStore` + `app.dependency_overrides[get_credential_store]`；
- [x] spec §6 的 12 组（含篡改密文 `mac_mismatch` 不写 verify_result、重建 canonical 抓"改业务行"、
      TTL 过期 410、清理只删旧对象、RSA provider）。

### Task 8: 前端

- [x] `types/finance.ts`：`VerifyStep/VerifyResult/DecryptPreview`；
- [x] `api/finance.ts`：`verifyReport`、`decryptReport`、`getPreviewReport`、`getPreviewFileBlob`；
- [x] `FinanceList.vue` 抽屉：`验签` 按钮 → 步骤 tags；`解密预览` → 字段摘要 + 每附件"查看影像"（blob URL）；
      过期/失败提示；按钮 loading 防重复；
- [x] `npm run typecheck && npm run build` 通过。

### Task 9: 真机验证 + 收口

- [x] 开发库提交 1 单 → `verify` 通过 → `decrypt` → `preview` JSON/影像可读 → 篡改密文 → verify 失败且无明文 →
      清理业务行与预览对象（审计保留）；
- [x] `alembic check`、全量测试；docs：API.md、PROGRESS、DECISIONS **D-087**、HANDOFF（下一步 S5-5）、AGENTS 锚点；
- [x] 提交 `feat(s5-4): 验签与解密（七步校验 + TTL 临时预览）`（push 由用户执行）。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 用存下的摘要"假验签" | `digest_recheck` 用 DB 数据 + canonical 里的 nonce 重建，字节级比对；专门用例 |
| MAC 失败却进入解密 | provider 的 `unwrap_symmetric` 先验 MAC 再解密；失败归类 `mac_mismatch`（50030） |
| 明文残留 | 预览对象只在 `previews/`，Redis 凭据 TTL + 清理任务；测试断言 DB 无明文列 |
| 口令泄露 | 只从 Settings 读；错误统一 422；日志/审计不含口令原文 |
| 多人同时 verify | 签发独立 token；审计 append；不改业务行 |
| 清理任务删错对象 | 只认前缀 + LastModified 早于 TTL；只删不写；失败告警 |