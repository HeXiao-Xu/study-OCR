# S5-4 验签与解密 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 8 章 S5-4（★关键）。
> 契约：`docs/API.md` §5（`POST /finance/reports/{id}/verify`、`POST /finance/reports/{id}/decrypt`、
> `GET /finance/preview/{preview_token}`）+ §10 错误码 + SCHEMA §5.2/§5.4/§5.5。
> 目标：财务在审核前**从库内原始数据重算**，证明"这单没被改过、确实是本人签的"；
> 通过才把明文放进**带 TTL 的临时预览**，库里不落明文。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S5-4 行。

## 1. 范围

本卡做验签 + 解密 + 临时预览（含 TTL 清理任务）；**不做**审核决策 approve/reject（S5-5）、归档（S5-6）。
前端在财务待审抽屉里接"验签 / 解密预览"。

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/services/verify_service.py` | 固定七步验签 + 失败归类 + `verify_token` 签发 + 审计 | 新 |
| `backend/app/services/preview_service.py` | 解密到临时对象、预览凭据、读取、TTL 清理 | 新 |
| `backend/app/services/credential_store.py` | `verify_token` / `preview_token` 的 Redis 存储协议 + 内存实现（测试注入） | 新 |
| `backend/app/services/report_crypto.py` | `open_sealed` 拆出**分步**接口（mac/解密归类）+ 从库里重算 canonical | 改 |
| `backend/app/services/storage.py` | `list_objects(prefix)`（清理任务用） | 改 |
| `backend/app/api/v1/finance.py` | `verify`、`decrypt`、`preview`（+文件子路径） | 改 |
| `backend/app/schemas/finance.py` | `VerifyStep/VerifyData/DecryptRequest/DecryptData` | 改 |
| `backend/app/core/config.py` + `.env.example` | `FINANCE_KEY_PASSPHRASE`、预览 TTL/清理间隔/前缀 | 改 |
| `backend/app/tasks/scheduler.py`（+ `services/preview_service.py`） | 预览临时对象清理任务（IntervalTrigger + 抢锁） | 改 |
| `backend/tests/test_verify.py` | 集成：七步/篡改/错口令/吊销/payload 不匹配/TTL/权限/审计 | 新 |
| `web/src/{types,api}/finance.ts`、`views/FinanceList.vue` | 抽屉里"验签 + 解密预览" | 改 |

**不动**：迁移（本卡**无 0009**）、0001~0008、密码算法参数、S5-2/S5-3 已定语义。

## 2. 与任务卡口述的偏差（以契约为准）

| # | 任务卡口述 | 契约 | 本卡执行 |
|---|---|---|---|
| E1 | 财务私钥口令"来自请求体" | API.md §5：入参为空对象，口令走**环境变量 `FINANCE_KEY_PASSPHRASE`**（D-016，不经 HTTP/日志） | 从 `Settings.finance_key_passphrase` 读；未配置/错误统一 `422/42200` |
| E2 | 审计 `action=finance_verify` | SCHEMA §9.19b 只有 `verify`（无 `finance_verify`） | 写 `action=verify`，`detail={result,reason_code,failed_step}` |
| E3 | 路径 `POST /finance/{id}/verify` | API.md §5：`POST /finance/reports/{id}/verify` | 按契约 |
| E4 | "用请求体口令 → S4-4 unseal" | 业务信封是 S5-2 的 `EnvelopeV1`（provider 版），S4-4 `envelope.seal` 不在业务路径 | 用 `report_crypto.open_sealed`（同一 AAD 重建） |
| E5 | 预览接口形状 | API.md 只定义了 `GET /finance/preview/{preview_token}` | 该 GET 返回**解密后的 report-v1 JSON**；另补 `GET /finance/preview/{token}/files/{file_id}` 返回单个影像（多张票必须有文件级寻址），写进 API.md |

## 3. verify：固定七步（顺序不可换）

```
envelope_lookup → mac → unwrap_cek → decrypt → cert_check → signature → digest_recheck
```

1. **envelope_lookup**：取单据级信封（`form_id` 非空）+ 当前 active 签名；缺失 → 409（`bad_envelope`）。
2. **unwrap_cek**：`provider.decrypt_key(wrapped_key, 财务私钥)`；失败 → 409 *（`bad_key`/`bad_envelope`）*。
   > 物理上必须先解 CEK 才能验 MAC（MAC key 由 CEK 派生，D-077），所以步骤顺序写"mac"在"解 CEK"之前，
   > 是实现口径的**契约顺序**；数据依赖上二者是"先解 CEK、再验 MAC 并解密"。响应里的 `steps` 按契约顺序输出，
   > 失败归类按真实原因（`mac_mismatch` / `bad_padding` / `digest_mismatch`）。
3. **mac**：`provider.unwrap_symmetric` 验 MAC；`AlgoIntegrityError(reason)` → `mac_mismatch`（50030）/`bad_padding`（50031）。
4. **decrypt**：同一调用完成解密；`bad_padding` 归此步（50031）。
5. **cert_check**：申请人证书（`signature.cert_id`）：吊销 → 过期/未生效 → 算法与签名一致；**不做链验签**（D-080）。
   失败 → `cert_invalid`（50021）。
6. **signature**：用证书公钥验 `canonical_json` 原字节；失败 → `bad_signature`（50021）。
7. **digest_recheck**（防"改了业务行但没改签名域"）：
   - 从 `report_forms.canonical_json` 解析出 **nonce**，用 `build_signing_payload` **从库内当前数据重算** canonical，
     与库里那份逐字节比较；不等 → `digest_mismatch`（50021）；
   - 重算 `SM3(canonical)` 必须等于 `signatures.digest_hex`；
   - 每个影像解密后的 `SHA-256` 必须等于 `invoice_files.sha256`，单据 JSON 解密后的 `payload_sha256` 已由
     `open_sealed` 校验；任一不符 → `digest_mismatch`；
   - `payload_sha256_match` 字段如实反映上述结果。

**成功**：写审计 `action=verify, result=ok` → commit → 签发 `verify_token`（Redis，30min）→ 200。
**失败**：写审计 `result=failed, reason_code, failed_step` → commit → 抛 `VerificationFailedError`（HTTP 409，
`code` 按类别、`data.reason_code/failed_step`）。`digest_mismatch`/`bad_signature`/`cert_invalid` 同时把
active 签名改为 `verified=false + verify_result=<reason>`（列表随即可见"失败"）；
`mac_mismatch`/`bad_padding` **不写** `signatures.verify_result`（SCHEMA §9 约定）。
**不返回任何明文**，错误文案不细到可利用。

## 4. decrypt：临时预览（TTL 30min）

- 入参 `verify_token`；校验 Redis 中 `finance:verify:{report_id}` 的值（不存在/过期 → 409）。
- 用同一财务私钥**重新解密**单据 JSON 与各影像（verify 时的明文不缓存——"通过才写入临时预览"）。
- 临时明文对象落 MinIO：`previews/{preview_token}/report.json`、`previews/{preview_token}/files/{file_id}.bin`；
  **数据库不写明文**；Redis 只存凭据元数据（`finance:preview:{token}` → `{report_id, created_at}`，TTL 30min）。
- 出参：`{preview_token, expires_at, ttl_seconds, content_type:"application/json", size, fields_preview}`
  （`fields_preview` = 解密后的 `report-v1` JSON：单据/明细/字段值/签名元数据）。
- `GET /finance/preview/{token}`：token 有效期内**可重复读取** → 返回 report JSON；过期 → `410/41001`。
- `GET /finance/preview/{token}/files/{file_id}`：返回该影像明文（`Content-Type` 取 `invoice_files.mime`）；
  文件必须属于该单据；过期 → 410。
- **TTL 清理任务**：APScheduler `IntervalTrigger`（默认 10min，`PREVIEW_CLEANUP_INTERVAL_SECONDS`）+
  Redis 锁（多副本只跑一个），`list_objects("previews/")` 删除 `LastModified` 早于 TTL 的对象；
  删除失败只告警。Redis 凭据靠自身 TTL 过期。

## 5. 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `FINANCE_KEY_PASSPHRASE` | 空 | 财务私钥口令；空则 `verify/decrypt` 返回 422（不启动即失败，避免演示环境被卡住） |
| `PREVIEW_TOKEN_TTL_SECONDS` | 1800 | 预览凭据与临时对象 TTL（契约 30min） |
| `PREVIEW_CLEANUP_INTERVAL_SECONDS` | 600 | 清理任务周期 |
| `PREVIEW_OBJECT_PREFIX` | `previews` | 临时对象前缀 |
| `PREVIEW_CLEANUP_LOCK_KEY` / `..._TTL_SECONDS` | `scheduler:lock:preview_cleanup` / 600 | 多副本去重锁 |

## 6. 测试（`backend/tests/test_verify.py`，`-m integration`）

复用 `test_submit` 的 SM 身份/FakeStorage/真提交链路；Redis 用**内存凭据存储**注入（不依赖真实 Redis）。

| # | 用例 | 断言 |
|---|---|---|
| 1 | 正常单 verify | 200、`verified=true`、七步全 ok、`cert_fp` 正确、`payload_sha256_match=true`、`verify_token` 非空、审计 `verify/ok`、`verify_chain ok` |
| 2 | 篡改 canonical（1 字节） | 409、`reason_code=digest_mismatch`、`failed_step=signature` 或 `digest_recheck`、无明文字段；签名行变 `verified=false/digest_mismatch`；列表 `verify_status=failed` |
| 3 | 篡改单据密文 1 bit | 409、`reason_code=mac_mismatch`、`failed_step=mac`；**不写** `signatures.verify_result` |
| 4 | 错误财务口令 | 422/42200（统一文案），不写审计成功行 |
| 5 | 吊销申请人证书 | 409/50021、`reason_code=cert_invalid`；签名行变 failed |
| 6 | 改业务行但 canonical 不变 | 重建 canonical 不一致 → 409 `digest_mismatch`（证明"不是拿存下的摘要直接比"） |
| 7 | decrypt 正常 | 200、`preview_token`、`size>0`、`fields_preview.report_no` 正确；MinIO 有 `previews/{token}/...`；DB 无明文列 |
| 8 | 预览读取 | `GET preview/{token}` 返回 JSON（report_no 正确）；`/files/{file_id}` 字节 == 原始明文影像；重复读取仍 200 |
| 9 | 凭据过期 | 内存 store 过期（或 TTL=0）→ decrypt 409；preview 410/41001 |
| 10 | 权限 | submitter/admin → 403；未知单据 → 404 |
| 11 | 清理任务 | FakeStorage 造两个对象（一旧一新）→ `cleanup_expired_previews` 只删旧的 |
| 12 | RSA provider | 同一流程用 `ALGO_PROVIDER=rsa` 走通 verify/decrypt（AES-GCM tag 长度、`key_algo=RSA-OAEP`） |
| 13 | 缺信封 | 删除单据信封 → 409 `bad_envelope` / `envelope_lookup` |

## 7. 验收

1. `pytest tests/test_verify.py -m integration` 全绿；全量单测/集成不回归；`alembic check` 零差异；
2. `npm run typecheck && npm run build` 通过；财务抽屉可"验签→解密预览→看图"；
3. 真机冒烟：提交 1 单 → verify 通过 → decrypt → 预览 JSON 与影像可读 → 篡改密文后 verify 失败且无明文 → 清理；
4. 文档：API.md（verify/decrypt/preview 备注 + 文件子路径）、PROGRESS、DECISIONS **D-087**、HANDOFF（下一步 S5-5）、AGENTS 锚点。

## 8. 已知限制

- 证书只做指纹/有效期/吊销/算法 + 签名绑定，**不做链验签、无 CRL/OCSP**（D-080，#93/#94）；
- `verify_token` 可重复用于 decrypt 直到 TTL 到期；S5-5 的 approve 才做"一次性消费"；
- 预览明文在 MinIO 有 TTL 清理（10min 扫描），**极端情况下最多残留一个清理周期**；Redis 凭据靠 TTL；
- 预览接口按 token 读取，token 即凭据（30min 内有效），不绑定调用者身份（财务常需多人对照；论文写"凭据即能力"）；
- 临时对象是明文且不在信封里，属于"审核窗口内的明文面"，论文"安全边界"如实写；
- **`GET /invoices/{id}/image` 保持"密文 409"不改**：财务明文只经 verify + decrypt 的临时预览流出
  （token 门槛 + `no-store`），避免"绕过审核窗口直接看原图"，也让明文访问可审计（D-087）。