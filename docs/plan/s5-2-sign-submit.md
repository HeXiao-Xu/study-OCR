# S5-2 签名 → 加密提交链路 — 实现计划（plan）

> 对应 spec：[`docs/spec/s5-2-sign-submit.md`](../spec/s5-2-sign-submit.md)｜
> 任务卡：实施计划第 8 章 S5-2（★关键）｜状态：**已实现（2026-11-21）**，Task 0–8 全部执行并验证
> 只动 `backend/app/{services,api/v1,schemas,core,crypto}`、`scripts/{register_identity.py,ca_init.sh}`、
> `backend/tests/{test_report_crypto,test_submit}.py` 与文档；**不动 0001~0008 迁移**（本卡无迁移）。

## Global Constraints

- 签名对象只能是 `canonical_json(...)` 的字节；`canonical_json` 原样落 `report_forms.canonical_json`；
- 随机数（nonce/CEK）只用 `secrets`；私钥/口令/CEK **不进日志、不进响应、不进异常信息**；
- 签名、封包走 `AlgoProvider`（默认 sm）；`digest_algo=SM3` 固定；
- 一个用例一个事务：提交要么全成要么全回滚（签名、信封、状态、审计同一事务）；
- 影像落 MinIO 只留密文；明文对象在**提交后**删除，删除失败只告警；
- 对外越权 404 / 角色级 403；错误码按 API.md §10；响应不含密文与密钥。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/crypto/key_loader.py` | SM（`.p8.enc` JSON）与 RSA（加密 PKCS#8 PEM）加载为 `KeyMaterial`；证书→收方材料；证书↔密钥校验 | 新 |
| `backend/app/services/key_service.py` | 证书/密钥行选取、`storage_path` 解析、口令解锁、证书有效性判定（转成 AppError） | 新 |
| `backend/app/services/report_crypto.py` | `build_signing_payload` / `build_sealed_report` / `seal_payload` / `open_sealed`（EnvelopeV1，provider 版） | 新 |
| `backend/app/services/submit_service.py` | `sign_report` / `submit_report`（状态、幂等、审计、影像转密文与清理） | 新 |
| `backend/app/schemas/report.py` | `SignRequest` / `SignData` / `SubmitRequest` / `SubmitData` | 改 |
| `backend/app/api/v1/reports.py` | `POST /{id}/sign`、`POST /{id}/submit` | 改 |
| `backend/app/services/report_service.py` | `transition(..., bump_version=False)` | 改 |
| `backend/app/core/config.py` + `.env.example` | `ALGO_PROVIDER=sm`、`KEY_STORAGE_ROOT=` | 改 |
| `backend/app/core/exceptions.py` | `SignatureFailedError(50020)` | 改 |
| `scripts/register_identity.py` | 证书/密钥登记（幂等，`--user` / `--server`） | 新 |
| `scripts/ca_init.sh` | `issue` 允许 `server` 角色 | 改 |
| `backend/tests/test_report_crypto.py` | 纯函数/封包（无 DB，默认套件） | 新 |
| `backend/tests/test_submit.py` | 集成（Docker 生成 SM 身份 + FakeStorage） | 新 |

---

### Task 0: 前提确认

- [x] `signatures` / `envelopes` / `certificates` / `key_records` 列在 0001 已建，**无需迁移**；
- [x] S4-2/S4-3/S4-4/S4-5/S4-6/S4-9 的封装可直接用（provider = 同接口）；
- [x] Docker 内 OpenSSL 3.5 可用（测试身份由 `req -x509 -newkey sm2` 现场生成，自签即可）；
- [x] `cryptography` 可生成 RSA 密钥与自签证书（RSA provider 路径的测试身份）。

### Task 1: `crypto/key_loader.py`

- [x] `KeyLoadError`（统一"私钥口令错误或密钥不可用"）；
- [x] `load_key_material(key_algo, storage_path, passphrase) -> KeyMaterial`：
      SM2 读 JSON→`EncryptedPrivateKey`→`load_keypair`→`SMProvider.key_from_hex`；
      RSA 读 PEM→`load_pem_private_key(password)`→PKCS#8（内存）→`KeyMaterial`；
- [x] `recipient_key_material(key_algo, cert_pem)`：SM 从证书 DER 取 128hex；RSA 取 SPKI PEM；
- [x] `certificate_problem(key_algo, cert_pem, public_material, *, revoked, now) -> str|None`：
      SM 走 `check_certificate`；RSA 走 cryptography（有效期/吊销/公钥 PEM 比对）。

### Task 2: `services/key_service.py`

- [x] `resolve_storage_path(storage_path, settings)`：空 `KEY_STORAGE_ROOT` = 仓库根；
      相对路径按根解析，绝对路径原样；解析结果必须落在根内（拒绝越界）；
- [x] `provider_for(settings)`：`get_provider(settings.algo_provider)`；
- [x] `get_signing_identity(db, user, provider)`：`key_records(user_id, key_type='sign', revoked_at IS NULL)`
      最新一条 + 证书；证书吊销/过期/公钥不符 → 409（消息区分）；无记录 → 409；
- [x] `get_finance_recipient(db)`：`certificates(owner_type='server', revoked=false, 有效期)` 最新一条；
      无 → 409；
- [x] `load_user_key(identity, passphrase)`：口令错误 → `422/42200` 统一文案；其它 → `500/50020`。

### Task 3: `services/report_crypto.py`

- [x] `build_signing_payload(form, items, invoices) -> dict`（spec §2.3，金额字符串、items 按 invoice_id 排序、
      nonce 由调用方传入以便测试确定性）；
- [x] `build_sealed_report(...) -> dict`（spec §2.4）；
- [x] `seal_payload(*, provider, recipient, recipient_cert_fp, plaintext) -> EnvelopeV1`
      （aad = canonical_json 六字段；CEK 用 `secrets`；返回 iv/mac/wrapped_key/ciphertext/sha256/key_algo/cipher_algo）；
- [x] `open_sealed(*, provider, private_key, sealed_row_dict) -> bytes`（S5-4 复用；本卡测试用）。

### Task 4: `services/submit_service.py` + schemas + 路由

- [x] `sign_report(db, user, report_id, passphrase)`：
      可见性/角色 → 状态闸门 → 明细与票据确认 → 证书/密钥 → 解锁 → 构建 payload → 签名 →
      旧 active 置 `void`（显式 UPDATE + flush）→ 插 signature（version=max+1）→
      写 `canonical_json/digest_*`、状态推到 `signed`、`version+1` → 审计 `sign` → commit；
- [x] `submit_report(db, user, report_id, payload, *, storage, settings)`：
      单据行 `FOR UPDATE` → 幂等（同键/异键/他人占用）→ 状态 `signed` → `version` 校验 →
      重算摘要 + 用用户证书验签（不符 → 409 带 reason）→ 财务证书 →
      封 JSON（单据信封）+ 逐文件封影像（文件信封、put 密文、改 `storage_key/is_cipher`）→
      `transition(encrypted)`+`transition(submitted)`（各 `bump_version=False`）→ `version+1` →
      审计 `seal` + `submit` → commit → 删旧明文对象（失败告警）；
- [x] 路由：`POST /{id}/sign` 200 `SignData`、`POST /{id}/submit` 200 `SubmitData`；
- [x] 异常：`SignatureFailedError(50020)`；口令错误 `42200`；缺证书/状态 `40900`。

### Task 5: 配置与脚本

- [x] `Settings.algo_provider: Literal["sm","rsa"] = "sm"`；`key_storage_root: str = ""`；
      `.env.example` 同步；
- [x] `scripts/register_identity.py`：`--user <name>` / `--server <name>`（`--cert`/`--key` 可覆盖），
      幂等（指纹/路径查重），打印登记结果；只写证书与密钥**登记**，不打印私钥；
- [x] `scripts/ca_init.sh`：`issue <name> server` 允许（OU=server），其余角色不变。

### Task 6: 测试

- [x] `test_report_crypto.py`：payload 确定性/排序/金额字符串；SM 与 RSA 的 `EnvelopeV1` 往返、
      形态（16B/32B、12B/16B）、aad 六字段任一篡改失败、错收方失败；密钥加载（SM/RSA、错口令）；
      证书校验（错公钥/吊销/RSA 配对）；
- [x] `test_submit.py`：spec §4.2 的 16 组，含 Docker 现场生成 SM 身份、RSA 身份用 cryptography、
      FakeStorage 断言"密文可解回明文、明文对象已删"、并发提交、篡改 canonical 的整体回滚。

### Task 7: 真实冒烟（开发库 + 真 MinIO）

- [x] 生成 finance 密钥（`keygen.py`）→ `ca_init.sh issue finance server` → `register_identity.py`
      登记 alice 与 finance；
- [x] 临时票据（真实 MinIO 对象）→ sign（`demo-pass-1234`）→ submit；
- [x] 断言：MinIO 引用对象非明文（可被财务私钥解回原文）、`envelopes` 无明文列、审计 3 行且链 OK；
- [x] 清理临时票据/单据对象与业务行（保留证书/密钥登记与审计行——前者是本卡应有产物，后者只增不改）。

### Task 8: 收口

- [x] API.md：S5-2 两条接口补实现备注（payload 字段、EnvelopeV1、幂等语义）；
- [x] SCHEMA.md §5.4 补一句"业务信封把 wrapped_key/payload_sha256 绑进 MAC 关联数据（D-085）"；
- [x] DECISIONS **D-085**；PROGRESS 勾选 + 产出表 + 变更日志 + 遗留 #108~；
- [x] AGENTS §10.8 加"签名与提交链路"锚点；HANDOFF 下一步 S5-3 + 环境/命令/指标；
- [x] 提交 `feat(s5-2): 签名与加密提交链路（provider 信封 + 影像密文化 + 幂等提交）`（push 由用户执行）。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 签名成功但封包失败留下半成品 | 同一事务；签名/信封/状态/审计一起 commit 或一起 rollback；测试断言"篡改 canonical 后无信封" |
| 明文对象残留 | 提交后删；失败告警；测试断言 FakeStorage 已删旧 key；论文写"尽力删除" |
| 重签撞 `uq_signatures_form_active` | 先显式 UPDATE 置 void + flush，再插入新行 |
| 并发提交重复封装 | 单据行 `FOR UPDATE` + 幂等键唯一约束；并发用例断言只落一套信封 |
| RSA 密钥加载格式不统一 | `key_loader` 统一入口；测试覆盖加密 PEM 与错口令 |
| 测试依赖 Docker（SM 身份/PostgreSQL） | 集成用例本就需要 Docker；Docker 不可用时 skip 并注明原因（不制造假绿） |