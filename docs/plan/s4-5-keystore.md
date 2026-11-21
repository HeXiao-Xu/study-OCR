# S4-5 密钥生成与私钥口令保护 — 实现计划（plan）

> 对应 spec：[`docs/spec/s4-5-keystore.md`](../spec/s4-5-keystore.md)｜
> 任务卡：实施计划第 7 章 S4-5｜状态：**已实现（2026-11-21）**，Task 0–4 全部执行并验证
> 只动 `backend/app/crypto/`、`backend/tests/test_crypto/`、`scripts/keygen.py` 与文档；
> 不碰业务接口、不加表/迁移（`key_records` 落库属 S5）。

## Global Constraints

- 私钥**不入库、不进日志、不进异常、不进接口响应**；明文只在内存，临时 PEM `finally` 删除；
- 密钥由 **OpenSSL 3.5 CLI**（`scripts/openssl.sh`）生成；`_kg` 只用于 dG 推导并与 OpenSSL 交叉核对；
- PBKDF2-HMAC-SHA256 ≥ 200000 次、16B 盐、32B KEK；AES-256-GCM（12B nonce），AAD 绑定元数据；
- 解锁失败统一 `KeystoreError("口令错误")`；空/短口令 `ValueError`；
- 不新增依赖（`cryptography` / `pycryptodomex` 已锁版本）。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/crypto/keystore.py` | `EncryptedPrivateKey` / `KeyPair` / `KeystoreError` / `derive_kek` / `key_fingerprint` / `private_key_hex_from_pkcs8` / `wrap_private_key` / `unwrap_private_key` / `load_keypair` | 新 |
| `backend/app/crypto/sm/sm2.py` | `public_key_from_private`（dG，复用校验与 `_kg`） | 改 |
| `backend/tests/test_crypto/test_keystore.py` | 13 组用例（spec §5） | 新 |
| `backend/tests/test_crypto/test_sm2.py` | 加 1 条：由标准向量私钥推导公钥 | 改 |
| `scripts/keygen.py` | 生成 → 加锁 → 解锁 → 签名自检（口令从 env/getpass） | 新 |
| `docs/{spec,plan}/s4-5-keystore.md`、`docs/{PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` | 收口 | 新/改 |

---

### Task 0: 探针（已执行）

- [x] `scripts/openssl.sh genpkey -algorithm SM2` → PKCS#8 PEM（`BEGIN PRIVATE KEY`）
- [x] PKCS#8 结构：`SEQUENCE{ int 0, AlgorithmIdentifier, OCTET STRING(ECPrivateKey) }`；
      `ECPrivateKey = SEQUENCE{ int 1, OCTET STRING d(32B), [1] BIT STRING pub }`
- [x] `DerSequence` 对非 INTEGER 成员返回**完整 TLV 字节**；`DerOctetString.decode(...).payload` 取内容
- [x] `d` 由 pycryptodomex 解出；`dG`（gmssl `_kg`）与 `openssl pkey -pubout -outform DER` 的
      公钥**逐字节一致**（`04` 前缀 + 128 hex）

---

### Task 1: `sm2.public_key_from_private`（先红后绿）

- [x] **Step 1**: `test_sm2.py` 加用例：`public_key_from_private(VECTOR_PRIVATE)` == `VECTOR_PUBLIC`；
      非法私钥 → `ValueError`。先红（无此函数）。
- [x] **Step 2**: 实现（校验 d → `_kg` → `_normalize_public_key` → 返回 128 hex）。
- [x] **Step 3**: 转绿。

---

### Task 2: `keystore.py`（先红后绿）

- [x] **Step 1**: 写 `test_keystore.py`（13 组用例，含程序构造 PKCS#8）。
- [x] **Step 2: 看它红**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_keystore.py -q)
# 期望：ModuleNotFoundError: No module named 'app.crypto.keystore'
```

- [x] **Step 3**: 实现（模型 + 六个函数；AAD 用 `canonical_json`；错误映射统一）。
- [x] **Step 4: 转绿**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto -q)
```

---

### Task 3: `scripts/keygen.py` 与真实 OpenSSL 验收

- [x] **Step 1**: 实现脚本（subprocess 调 `bash scripts/openssl.sh`；临时目录在 `secrets/keys/`；
      口令 env/交互；输出只含公开信息；自检签名）。
- [x] **Step 2: 真实跑一遍**

```bash
STUDYOCR_KEY_PASSPHRASE='demo-pass-1234' .venv/Scripts/python.exe scripts/keygen.py --name alice --role submitter
```

- [x] **Step 3: 独立验签（OpenSSL 验我们 gmssl 的签名）**

```bash
# 用 keygen 产出的 sign.pub.pem 验签（脚本自检里已签一个固定消息；也可手工复现）
bash scripts/openssl.sh pkeyutl -verify -pubin -inkey secrets/keys/alice/sign.pub.pem \
  -rawin -digest sm3 -in <message> -sigfile <sig.der>
```

- [x] **Step 4: 明文与口令检查**

```bash
grep -rn "PRIVATE KEY" backend/ docs/ || echo "（无命中）"
ls -l secrets/keys/alice/            # 只有 .p8.enc 与 .pub.pem，无明文 PEM
```

---

### Task 4: 回归、文档与提交

- [x] **Step 1**: `pytest tests/test_crypto -q` + 全量 `pytest` + `alembic check`。
- [x] **Step 2**: PROGRESS（勾选 + 产出/验收 + 遗留）、DECISIONS **D-079**（密钥生成与口令保护口径、
      SCHEMA `key_records` 与任务卡字段的差异）、AGENTS §10.8 锚点、HANDOFF（下一步 S4-6）。
- [x] **Step 3**: 提交 `feat(s4-5): SM2 密钥生成与私钥口令保护（PBKDF2+AES-GCM）`；**push 由用户做**。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| Docker 未启动导致 keygen 失败 | 脚本检查 `docker info` 并给明确提示；单测不依赖 Docker |
| PKCS#8 结构变化（OpenSSL 版本） | 解析失败有明确 `ValueError`；Task 3 每次用真 OpenSSL 复验 |
| 口令进 shell 历史 | 只收 env 或 `getpass`，拒绝命令行参数 |
| 临时 PEM 残留 | `finally` 删除；`secrets/` 已 gitignore；Task 3 用 `ls` 复核 |
| GCM AAD 与文件字段不一致 | 解包一律按**文件里的** iterations/kdf/algo 重算 AAD；篡改必失败（用例 7） |