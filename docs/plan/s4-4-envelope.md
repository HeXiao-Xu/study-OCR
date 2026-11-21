# S4-4 数字信封 — 实现计划（plan）

> 对应 spec：[`docs/spec/s4-4-envelope.md`](../spec/s4-4-envelope.md)｜
> 任务卡：实施计划第 7 章 S4-4｜状态：**已实现（2026-11-21）**，Task 0–5 全部执行并验证
> 只动 `backend/app/crypto/`、`backend/tests/test_crypto/`、`requirements.txt` 与文档；不碰业务接口、不加表/迁移。

## Global Constraints

- 会话密钥（CEK）每次信封重新生成，只来自 `secrets`；SM2 只包 16B CEK（明文 1–32B，超长 `ValueError`）；
- SM2 用 **C1C3C2**、C1 **不带 04 前缀**；K 用 `secrets`（**不用** gmssl `encrypt` 的 `func.random_hex`）；
- 信封完整性：`mac = HMAC-SM3(mac_key, header ‖ wrapped_key ‖ IV ‖ ciphertext ‖ recipient_cert_fp)`
  + `payload_sha256`；解密顺序：解 CEK → 验 MAC 解密 → sha256 比对；
- 失败必须归类：`bad_envelope` / `mac_mismatch` / `bad_padding` / `digest_mismatch`；
- 2MB seal→open **< 1s**：SM4 走 `cryptography`（OpenSSL），SM3/HMAC 走 `hashlib`（OpenSSL），
  gmssl 保留回退；`cryptography==50.0.2` 补进 `requirements.txt`（已在环境与 lock 里）。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/crypto/sm/sm3.py` | OpenSSL 快路径 + gmssl 回退；`hmac_sm3` 同步两条路径 | 改 |
| `backend/app/crypto/sm/sm4.py` | CBC/ECB 改 `cryptography`；新增 `trailer`（MAC 尾部绑定） | 改 |
| `backend/app/crypto/sm/sm2.py` | `encrypt_bytes` / `decrypt_bytes`（C1C3C2、KDF、≤32B） | 改 |
| `backend/app/crypto/envelope.py` | `Envelope` / `EnvelopeError` / `seal` / `open_sealed` | 新 |
| `backend/requirements.txt` | `cryptography==50.0.2` | 改 |
| `backend/tests/test_crypto/{test_sm3,test_sm4,test_sm2}.py` | 回退路径、trailer、SM2 加解密 | 改 |
| `backend/tests/test_crypto/test_envelope.py` | 信封全套 + 2MB 性能 | 新 |

---

### Task 0: 性能探针（已执行）

- [x] 纯 Python：SM4 2MB 加密 14.0s / 解密 14.1s；SM3 2MB 7.5s（往返 28.1s，不达标）
- [x] OpenSSL：`cryptography` SM4-CBC 2MB 0.029s/0.015s；`hashlib.new("sm3")` 2MB 0.008s
- [x] `cryptography==50.0.2` 已在环境与 `requirements.lock.txt`；国标 ECB 向量在 OpenSSL 下同样成立

---

### Task 1: `sm3.py` 双后端（先红后绿）

- [x] **Step 1**: 加两条回退路径用例（monkeypatch `_OPENSSL_SM3=False`，仍须命中
  `abc`/512bit/空串与 4 组 HMAC 向量）→ 先红（标志不存在）。
- [x] **Step 2**: 实现 `_OPENSSL_SM3 = "sm3" in hashlib.algorithms_available`；
  `sm3_digest` → `hashlib.new("sm3", payload)`；否则原 gmssl 路径；
  `hmac_sm3` → `hmac.new(key, data, "sm3")`；否则手写 RFC 2104。
- [x] **Step 3**: 转绿 + 全量 crypto 不回归。

---

### Task 2: `sm4.py` 换 OpenSSL 后端 + `trailer`

- [x] **Step 1**: 先写 `trailer` 用例（换 trailer → `mac_mismatch`）→ 先红（无此参数）。
- [x] **Step 2**: `_encrypt_block`/`_cbc_encrypt`/`_cbc_decrypt` 改用
  `Cipher(algorithms.SM4(key), modes.ECB()/CBC(iv))`；`encrypt/decrypt` 增加 `trailer: bytes = b""`；
  MAC 输入 `aad ‖ IV ‖ 密文 ‖ trailer`。
- [x] **Step 3**: 转绿；国标向量、OpenSSL 交叉向量、篡改用例全过。

---

### Task 3: SM2 加解密

- [x] **Step 1**: 先写用例（往返、>32B、同明文两次不同、篡改、C1 不在曲线、
  与 gmssl `CryptSM2.encrypt/decrypt` 互操作）→ 先红（无 `encrypt_bytes`）。
- [x] **Step 2**: 实现（C1‖C3‖C2；KDF 用 gmssl `sm3_kdf`；K 用 secrets；KDF 全零重试）。
- [x] **Step 3**: 转绿。

---

### Task 4: `envelope.py`

- [x] **Step 1**: 写 `test_envelope.py`（spec §6 表）→ 先红（模块不存在）。
- [x] **Step 2**: 实现 `Envelope` / `EnvelopeError` / `seal` / `open_sealed`（顺序与 MAC 覆盖按 spec）。
- [x] **Step 3**: 转绿；跑 2MB 用例记录实测耗时。

---

### Task 5: 验收、文档与提交

- [x] **Step 1**: 回归

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto -q)
(cd backend && ../.venv/Scripts/python.exe -m pytest -q)
(cd backend && ../.venv/Scripts/python.exe -m alembic check)
```

- [x] **Step 2**: 2MB 实测数字、SM2 互操作结果写进 PROGRESS；DECISIONS **D-077**
  （OpenSSL 后端 + cryptography 依赖 + SM2 加解密自组合的理由 + C3/MAC 覆盖取舍）；
  AGENTS §10.8 补信封锚点；HANDOFF 更新（S4-5）。
- [x] **Step 3**: 提交 `feat(s4-4): 数字信封（SM2 包裹 CEK + SM4-CBC EtM）`；**push 由用户做**。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| OpenSSL 环境缺 SM3/SM4 | sm3 有 gmssl 回退；sm4 若 `algorithms.SM4` 缺失则报错并回退到 gmssl 实现（保留在 `_gmssl_*` 内部函数） |
| 2MB 用例在慢机器上超 1s | 断言给 1.0s（实测约 0.1s，10 倍余量）；失败要先看是否退化到纯 Python |
| MAC 覆盖顺序与 SCHEMA 不一致 | 采用"SCHEMA 字段全绑 + wrapped_key"的超集，顺序写死在 spec/代码注释 |
| SM2 密文长度歧义（C1 首字节 0x04） | 不做自动前缀剥离；格式由本卡定死（64+32+n） |