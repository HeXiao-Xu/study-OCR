# S5-2 签名 → 加密提交链路 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 8 章 S5-2（阶段 5 第二张，★关键）。
> 契约：`docs/API.md` §4（`POST /reports/{id}/sign`、`POST /reports/{id}/submit`）
> + `docs/SCHEMA.md` §5.3/§5.4（`signatures` / `envelopes`）+ §2.2/§2.3（`certificates` / `key_records`）。
> 目标：把 S5-1 建好的单据**一次事务**内走完"规范化 → 签名 → 封数字信封 → 推进状态 → 审计"，
> 影像落 MinIO 只留密文；为 S5-4 的验签/解密留下可复算的数据。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S5-2 行。

## 1. 范围

本卡做 `sign` 与 `submit` 两个动作；**不做**财务端列表/验签/解密/审核（S5-3~S5-5）、
归档（S5-6）、驳回重提的再次提交（S5-5 处理信封唯一约束的复用问题）。

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/services/submit_service.py` | `sign_report` / `submit_report`（事务、状态、幂等、审计） | 新 |
| `backend/app/services/report_crypto.py` | 签名域/封包明文构造 + `EnvelopeV1` 封装（走 provider） | 新 |
| `backend/app/services/key_service.py` | 证书/密钥选取、`storage_path` 解析、口令解锁、证书校验 | 新 |
| `backend/app/crypto/key_loader.py` | SM（keystore JSON）与 RSA（加密 PKCS#8 PEM）密钥材料加载；证书↔公钥校验 | 新 |
| `backend/app/schemas/report.py` | `SignRequest`/`SignData`/`SubmitRequest`/`SubmitData` | 改 |
| `backend/app/api/v1/reports.py` | 两条路由 | 改 |
| `backend/app/services/report_service.py` | `transition(..., bump_version=False)`（供本卡一次操作只 +1） | 改 |
| `backend/app/core/config.py` + `.env.example` | `ALGO_PROVIDER`（sm/rsa）、`KEY_STORAGE_ROOT` | 改 |
| `backend/app/core/exceptions.py` | `SignatureFailedError`（50020） | 改 |
| `scripts/register_identity.py` | 把证书/密钥登记进 `certificates` / `key_records`（幂等） | 新 |
| `scripts/ca_init.sh` | `issue` 允许 `server` 角色（财务服务器证书） | 改 |
| `backend/tests/test_report_crypto.py` | 纯函数/封包往返（默认套件，无 DB） | 新 |
| `backend/tests/test_submit.py` | 集成：签名/提交/幂等/回滚/篡改/并发/双 provider | 新 |

**不动**：0001~0008 迁移（本卡**不需要迁移 0009**——所有列 0001 已建）、国密/SM 算法参数、
`report_items`/`report_forms` 列、S5-1 的建单/明细语义。

## 2. 关键设计决策

### 2.1 业务链路走 `AlgoProvider`（D-083 / #100 的兑现）

签名与信封**不直接调国密封装**，而是 `get_provider(settings.algo_provider)`（默认 `sm`）：

| 动作 | provider 方法 | SM（默认） | RSA（对照） |
|---|---|---|---|
| 签名 | `sign/verify` | SM2-SM3（DER） | RSA-PSS-SHA256 |
| 包 CEK | `encrypt_key/decrypt_key` | SM2 C1C3C2 | RSA-OAEP-SHA256 |
| 对称 | `wrap_symmetric/unwrap_symmetric` | SM4-CBC+HMAC-SM3 | AES-256-GCM |

`signatures.algo`、`envelopes.key_algo`/`cipher_algo` 按 provider 落实际值（SCHEMA §9.16/§9.17 都支持）。
**`digest_algo` 固定 `SM3`、`digest_hex = SM3(canonical_bytes)`**（API.md 明说它只用于展示/快速比对，
不是验签依据）；RSA 的 PSS 内部仍是 SHA-256，但不改这两列语义。

### 2.2 `EnvelopeV1` 封装格式（provider 版，替代 `crypto/envelope.py` 作为业务路径）

`crypto/envelope.py::seal/open_sealed` 是 S4-4 的**国密参考实现**（保留、测试继续跑），
但业务路径要"同接口换算法"，所以按 provider 定义 `EnvelopeV1`：

```
cek         = secrets.token_bytes(provider.symmetric_key_bytes)   # SM 16B / GCM 32B
wrapped_key = provider.encrypt_key(cek, 收方 KeyMaterial)
aad         = canonical_json({
                "version": "envelope-v1",
                "key_algo": "SM2" | "RSA-OAEP",
                "cipher_algo": "SM4-CBC-HMAC-SM3" | "AES-256-GCM",
                "recipient_cert_fp": <64hex>,
                "wrapped_key_b64": <b64>,
                "payload_sha256": <64hex>,
              })
payload     = provider.wrap_symmetric(cek, plaintext, aad=aad)
row         = iv_b64=payload.nonce, mac_b64=payload.tag, ciphertext_b64=b64(ct)
              （或 ciphertext_storage_key=对象名）, payload_sha256
```

- **aad 绑定了 `wrapped_key` 与 `payload_sha256`**：单改包裹密钥 / 载荷摘要都会让 MAC（或 GCM tag）失配，
  与 S4-4 信封"MAC 覆盖 wrapped_key"的强度对齐（D-077 的教训）；
- 拆封顺序（S5-4 用）：`provider.decrypt_key` 解 CEK → `provider.unwrap_symmetric(aad 重算)` → `sha256` 比对；
- `aad` 的全部字段都来自 `envelopes` 行，**可无损重建**。

### 2.3 签名域（payload v1）

`canonical_json` 的字节即签名对象（AGENTS §10.4：禁止签 dict、禁止签展示文本）：

```json
{
  "payload_version": "v1",
  "report_no": "RPT-202611-0001",
  "applicant_id": 12,
  "title": "2026-11 差旅报销",
  "reason": "学术会议",
  "total_amount": "60.00",
  "item_count": 1,
  "created_at": "2026-11-21T10:02:03.123456+00:00",
  "nonce": "<32 hex>",
  "items": [
    {"invoice_code": "1234567890", "invoice_no": "12345678",
     "amount": "60.00", "file_sha256": "<64 hex>"}
  ]
}
```

- 金额一律**字符串**（canonical 拒 float/Decimal）；items 按 `invoice_id asc` 排序保证确定性；
- `nonce` 用 `secrets.token_hex(16)`，每次签名重新生成（重签必然得到不同 canonical）；
- 时间统一 `astimezone(utc).isoformat()`；
- `canonical_json` **原样字符串**写 `report_forms.canonical_json`（TEXT，SCHEMA 定稿 #15），
  `digest_hex = SM3(utf8(canonical))`。
- S5-4 重新拼装的唯一入口是 `report_crypto.build_signing_payload`（同一函数），
  验签优先用库里存的 `canonical_json` 原字节。

### 2.4 封包明文（`report-v1`，给财务端看的那份）

`submit` 时把"单据 + 明细 + 字段 + 签名元数据"封成一份 JSON（UTF-8 字节）落 `ciphertext_b64`：

```json
{
  "payload_version": "report-v1",
  "report_no": "...", "applicant": {"id": 12, "username": "alice", "dept": "计算机学院"},
  "title": "...", "reason": "...", "total_amount": "60.00", "item_count": 1,
  "created_at": "...", "submitted_at": "...",
  "items": [{"invoice_id": 3, "invoice_code": "...", "invoice_no": "...", "invoice_date": "2026-03-01",
             "buyer_name": "...", "seller_name": "...", "amount": "...", "tax_amount": "...",
             "total_amount": "...", "file_sha256": "...", "fields": {"total_amount": "60.00", "...": "..."}}],
  "signature": {"algo": "SM2", "digest_hex": "...", "signature_b64": "...", "cert_fp": "..."}
}
```

- 封的是 canonical JSON 字节（不是给人看的排版），`payload_sha256 = SHA-256(plaintext_bytes)`；
- **影像本体单独封**：每个明细票据的文件一个**文件级信封**（`envelopes.form_id=NULL, file_id=<file>`，
  `ciphertext_storage_key=<MinIO 密文对象名>`），各用独立 CEK；
- 单据级信封（`form_id=单据 id`）只装这份 JSON（`ciphertext_b64`）——符合 SCHEMA §5.4"至少一个非空"。

### 2.5 影像改为密文落盘（MinIO 只留密文）

对每个明细文件：

1. `storage.get_bytes(旧 storage_key)` 读明文（≤ `MAX_UPLOAD_MB`）；
2. `seal` → 密文字节 → `storage.put(新对象名 "reports/{yyyy}/{mm}/{hex}.bin.cipher")`；
3. 更新 `invoice_files.storage_key = 新对象`、`is_cipher = True`（同事务）；
4. **提交成功（commit）之后**才删旧明文对象；删除失败只告警（与 D-071 的删除同理，不把已提交事务回滚）。

于是 `GET /invoices/{id}/image` 在提交后按既有闸门返回 **409**（密文），
S5-4 再把它改成"内存解密 → 渲染 → 释放"。

> 为什么更新 `is_cipher/storage_key`：SCHEMA §3.1 明确 `storage_key` 指向的是**密文**，
> 若只新增信封而保留明文对象，等于"库里登记密文、桶里还躺着明文"，这正是任务卡要求排除的。

### 2.6 证书与密钥来源（登记脚本，不新增表/列）

- `certificates`：用户证书 `owner_type='user'`（`owner_id=users.id`）；财务证书 `owner_type='server'`；
- `key_records`：`sign`（用户，`user_id` 非空）/ `envelope`（财务服务器，`user_id` 为空）；
  **只登记 `storage_path`**（仓库相对 `secrets/...`，或绝对路径），私钥本体不入库；
- `scripts/register_identity.py`：解析 PEM → `certificates` 行（指纹查重，幂等）→ `key_records` 行；
  用户侧用 `users.username` 定位 `owner_id`，打印登记结果；`--server` 只登记证书与信封密钥；
- 文件格式：SM 用 S4-5 的 `.p8.enc`（JSON：PBKDF2+AESGCM）；RSA 用**口令加密的 PKCS#8 PEM**
  （`cryptography` 的 `BestAvailableEncryption`），由 `crypto/key_loader.py` 统一加载；
- `KEY_STORAGE_ROOT`（默认空）→ 空表示"仓库根"；相对路径按仓库根解析，绝对路径原样使用；
  解析后必须落在该根内（拒绝 `../` 越界）。

### 2.7 状态与版本

- `sign`：允许 `draft`/`pending_sign`/`rejected`；明细为空或票据未确认 → `409`。
  `draft`/`rejected` 先 `transition(pending_sign)` 再 `transition(signed)`；已 `signed` 直接重签；
- `submit`：要求 `signed`；事务内 `transition(encrypted)` → `transition(submitted)`；
- **一次 HTTP 操作 `report_forms.version` 只 +1**（`transition` 新增 `bump_version=False`，
  由操作末尾统一 +1），避免"一次签名跳 3 个版本"的噪音（S5-1 的默认行为不变）。

### 2.8 幂等与重复提交

| 情形 | 结果 |
|---|---|
| 同一单据、同一 `idempotency_key`、已提交 | **200 + `duplicated=true`** + 既有单号/签名/信封 id（不重复签名、不重复封装） |
| 同一单据、已有别的 `idempotency_key`、已提交 | `409/40900`（状态不允许） |
| 别的单据已占用该 `idempotency_key` | `409/40900`，`data.reason="idempotency_key_reused"`（`UNIQUE` 兜底） |
| 并发两次提交同一单据 | 单据行 `FOR UPDATE` 串行化：一个 200、一个 409；信封只落一套 |

### 2.9 审计（同事务）

`sign` → `action=sign`（`target_type=signature`）；`submit` → 先 `action=seal`（`target_type=envelope`），
再 `action=submit`（`target_type=form`）。`detail_json` 只放单号/摘要/证书 id/文件数，不放明文与密钥。

## 3. 错误码对照（API.md §4 + §10）

| 场景 | HTTP/code |
|---|---|
| 单据/成员不存在或非本人 | `404/40400` |
| 角色不对（reviewer 在可见单据上操作） | `403/40301`；admin 由仓库层 404 |
| 状态不允许（未签名提交、已提交再改、明细为空、票据未确认） | `409/40900` |
| 无有效证书/密钥/财务收方证书 | `409/40900`（消息说明缺哪类） |
| 口令错误（统一文案，不区分原因） | `422/42200` |
| 签名运算异常 | `500/50020`（`SignatureFailedError`） |
| 已存 canonical 与签名不符（被篡改） | `409/40900`，`data.reason` ∈ `digest_mismatch`/`bad_signature` |

## 4. 测试

### 4.1 纯函数/封包（`test_report_crypto.py`，默认套件）

| 用例 | 断言 |
|---|---|
| payload 确定性 | 同输入两次（同 nonce）canonical 字节相同；items 乱序输入按 invoice_id 排序后一致；金额是字符串且被 canonical 接受 |
| `EnvelopeV1` SM 往返 | `seal` → `open` 还原明文；nonce 16B、tag 32B；同明文两次 IV/CT/MAC 不同 |
| `EnvelopeV1` RSA 往返 | 同上（nonce 12B、tag 16B） |
| aad 绑定 | 改 wrapped_key / ciphertext / iv / payload_sha256 / 收方指纹任一 → 解封失败（`mac_mismatch`/tag 错） |
| 错收方私钥 | SM 解封失败；RSA 解封失败 |
| 密钥加载 | SM：临时 `.p8.enc` 用口令加载 → KeyMaterial 指纹一致；错口令 → 统一错误 |
| RSA 密钥加载 | 加密 PKCS#8 PEM 加载 → KeyMaterial；错口令 → 统一错误 |
| 证书校验 | SM：committed alice 夹具 + 错公钥 → `cert_public_key_mismatch`；吊销 → `cert_revoked`；RSA：配对 OK / 不配对失败 |

### 4.2 HTTP 集成（`test_submit.py`，`-m integration`，临时库 + 假 MinIO）

SM 身份由 Docker OpenSSL 现场生成（`req -x509 -newkey sm2`，自签即可——运行期不做链验签，D-080），
私钥用 `wrap_private_key` 落临时 `.p8.enc`；RSA 身份由 `cryptography` 生成（含自签证书）。
存储用内存 FakeStorage（`put/get_bytes/delete/exists/build_object_key`）。

| # | 用例 | 断言 |
|---|---|---|
| 1 | sign 成功 | 200；`status=signed`；`canonical_json` 落库且 = 响应 `canonical_sha256` 的来源；`digest_hex=SM3(canonical)`；签名行 `active`、`version=1`、`cert_id` 正确；audit `sign`；`verify_chain ok` |
| 2 | 签名可用证书公钥验证 | 用 provider.verify(canonical, sig, 证书公钥) == True（不信任服务自报） |
| 3 | 无明细 / 票据未确认 | 409/40900，不落签名行 |
| 4 | 状态不允许（已提交） | 409/40900 |
| 5 | 越权 | 他人 404；reviewer（已提交单可见）403；admin 404 |
| 6 | 口令错误 | 422/42200 统一文案；无签名行；状态/版本不变 |
| 7 | 无证书 / 证书吊销 | 409/40900（消息区分），无半成品 |
| 8 | 重签 | 旧签名 `void`、新 `active version=2`；canonical 因 nonce 变化；form.version 只 +1 |
| 9 | submit 成功 | 200；`status=submitted`、`submitted_at`；单据信封 1 行 + 文件信封 N 行；`wrapped_key/iv/mac/payload_sha256` 齐全；**响应无密文与密钥** |
| 10 | 影像变密文 | `invoice_files.is_cipher=true`、`storage_key` 指向新对象；FakeStorage 中该对象字节 ≠ 明文且可被财务私钥解回明文；旧明文对象已删除 |
| 11 | 签名校验闸门 | 提交前篡改 `canonical_json` → 409 且**没有信封/状态变化**（整体回滚） |
| 12 | 幂等 | 同 key 再提交 → 200 `duplicated=true`、id 不变、无新信封；别的单据占用同 key → 409 `reason=idempotency_key_reused` |
| 13 | 未签名提交 / version 不符 | 409 |
| 14 | 并发提交 | 两线程同单不同 key → 恰好 1 成功 1 冲突；信封只有一套 |
| 15 | RSA provider | `Settings(ALGO_PROVIDER="rsa")`：签名 `algo=RSA-PSS`；信封 `key_algo=RSA-OAEP`、`cipher_algo=AES-256-GCM`；可用财务 RSA 私钥解开 |
| 16 | 详情 | 提交后 `GET /reports/{id}` 返回 `signature`/`envelope` 元数据，仍无 `ciphertext_b64`/`wrapped_key_b64`/`storage_key` |

## 5. 验收

1. `pytest tests/test_submit.py -m integration` 与 `pytest tests/test_report_crypto.py` 全绿；
2. 全量单测/集成不回归；`alembic check` 零差异（本卡无迁移）；
3. 真机冒烟：登记 alice/finance → 临时票据 → sign（`demo-pass-1234`）→ submit →
   MinIO 引用对象是密文、`envelopes` 无明文、`audit` 增 3 行且 `verify_chain ok`；随后清理业务行；
4. 文档：API.md（S5-2 备注）、PROGRESS、DECISIONS **D-085**、HANDOFF（下一步 S5-3）、AGENTS 锚点。

## 6. 已知限制

- 信封"一单一套"由 `uq_envelopes_form`/`uq_envelopes_file` 兜底：**驳回重提**（S5-5）需要复用或作废旧信封，
  本卡不支持二次提交（提交后状态机不允许回退），S5-5 再定义；
- 私钥口令仍由调用方提供（API 契约如此），服务端不托管；RSA 业务密钥虽是加密 PEM，
  但**没有密钥轮换/多 key 验证**（沿用 #99 的边界）；
- 提交是同步的：影像大时（2MB/张）耗时随张数线性增长（S6 可压测）；
- `crypto/envelope.py`（S4-4 参考实现）与业务 `EnvelopeV1` 是两种字节布局：
  论文写"协议参考实现 vs 业务可替换实现"，S5-4 只解后者。**两者都保留测试**；
- 审计 hash 链只在本卡增长；S4-7 的能力边界（无外部锚）不变。