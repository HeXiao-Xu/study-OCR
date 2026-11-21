# S4-9 RSA-OAEP+PSS 与 AES-GCM 对照实现 — 实现计划（plan）

> 对应 spec：[`docs/spec/s4-9-provider-rsa-gcm.md`](../spec/s4-9-provider-rsa-gcm.md)｜
> 任务卡：实施计划第 7 章 S4-9｜状态：**已实现（2026-11-21）**，Task 0–5 全部执行并验证
> 只动 `backend/app/crypto/{rsa_layer,aesgcm_layer,provider}.py`、`backend/tests/test_crypto/`、
> `scripts/crypto_bench_compare.py` 与文档；**不改国密层、不改 `envelope.py`**。

## Global Constraints

- 两套实现**接口签名完全一致**（`AlgoProvider`），否则 S6 对比不成立；
- OAEP 与 PSS 的 hash / MGF1 hash **全部显式 SHA-256**；RSA 只包 16B CEK；
- 随机数只许 `secrets`（GCM nonce 12B、SM4 IV 仍由 S4-3 内部 `secrets`）；
- 完整性失败统一 `AlgoIntegrityError(reason)`（参数化测试断言同一类型）；
- 默认 provider = 国密；SM 密钥仍由 `scripts/keygen.py`/OpenSSL 生成（provider 不造密钥）。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/crypto/rsa_layer.py` | `generate_keypair` / `sign` / `verify` / `encrypt_key` / `decrypt_key`（PEM 字节进出，显式 SHA-256） | 新 |
| `backend/app/crypto/aesgcm_layer.py` | `encrypt(key32, plaintext, aad) -> (nonce, ct, tag)`、`decrypt`（`InvalidTag` → `AesGcmIntegrityError` 或直接透出） | 新 |
| `backend/app/crypto/provider.py` | `AlgoProvider` / `KeyMaterial` / `SymmetricPayload` / `AlgoIntegrityError` / `SMProvider` / `RSAProvider` / `get_provider` | 新 |
| `backend/tests/test_crypto/test_provider_rsa_gcm.py` | 参数化两套实现（往返/篡改/长度/工厂/协议） | 新 |
| `scripts/crypto_bench_compare.py` | 两套实现 p50/p95 对拍 | 新 |
| `docs/{spec,plan}/s4-9-provider-rsa-gcm.md`、`docs/{PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` | 收口 | 新/改 |

---

### Task 0: 依赖与前提（已确认）

- [x] `cryptography==50.0.2` 已装（支持 RSA 生成/OAEP/PSS/AES-GCM），**无需新依赖**；
- [x] SM2/SM4 封装（S4-2/S4-3）已在，SMProvider 直接复用；不做签名/加密的重写。

### Task 1: `rsa_layer.py`

- [x] `generate_keypair(bits)`：只允许 2048/3072，返回 `(private_pem, public_pem)`；
- [x] `sign/verify`：PSS(MGF1-SHA256, salt=DIGEST_LENGTH)，显式 SHA-256；`verify` 吞 `InvalidSignature` 返回 False；
- [x] `encrypt_key/decrypt_key`：OAEP(MGF1-SHA256, SHA-256)；明文 1–32B 校验；
- [x] 单测：往返/篡改/错钥/超长/非法 bits。

### Task 2: `aesgcm_layer.py`

- [x] `encrypt`：`secrets.token_bytes(12)` nonce、AESGCM、返回 `(nonce, ct, tag)`（tag 16B）；
- [x] `decrypt`：`InvalidTag` → 明确异常；key 必须 32B、nonce 12B、tag 16B；
- [x] 单测：往返/篡改/错钥/同明文两次 nonce 不同/长度校验。

### Task 3: `provider.py`

- [x] 协议与 dataclass；`SMProvider`（sign/verify/encrypt_key/decrypt_key/wrap/unwrap；keygen 抛 NotImplementedError；`from_hex`）；
- [x] `RSAProvider`（keygen/sign/verify/encrypt_key/decrypt_key/wrap/unwrap；`InvalidTag` → `AlgoIntegrityError("mac_mismatch")`）；
- [x] `get_provider("sm"|"rsa")`；`isinstance(x, AlgoProvider)` 成立（`runtime_checkable`）。

### Task 4: 参数化测试 `test_provider_rsa_gcm.py`

- [x] `pytest.param(SMProvider(), id="sm")` / `pytest.param(RSAProvider(2048), id="rsa-2048")` 跑同一组：
      签名往返、载荷/签名篡改、错钥、跨 provider、CEK 包裹往返/篡改、对称往返（中文+64KB）/篡改/aad；
- [x] 各自规格断言（SM tag 32B/nonce 16B；GCM tag 16B/nonce 12B）；
- [x] `pytest -k "rsa or gcm"` 全绿（任务卡验收命令）。

### Task 5: 基准脚本与收口

- [x] `scripts/crypto_bench_compare.py`：SM 密钥临时生成（Docker）→ 两套实现各跑 N 次 → JSON + 表格（p50/p95）；
- [x] 真跑一次，数字写进 PROGRESS；
- [x] PROGRESS（S4-9 完成 → **阶段 4 全部完成**，下一步 S5-1）、DECISIONS **D-083**、
      AGENTS §10.8「对照算法层」锚点、HANDOFF（下一阶段 S5）；
- [x] 全量单测/集成/`alembic check`；提交 `feat(s4-9): RSA-OAEP/PSS 与 AES-GCM 对照实现 + provider`。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 两套接口漂移 | 参数化测试 + `isinstance(.., AlgoProvider)`；接口只在 provider.py 定义 |
| OAEP/PSS 默认 hash 不一致 | 全部显式 `SHA256()` + MGF1(SHA256) |
| RSA 2048 装不下大 CEK | 只收 ≤32B；测试超长报错 |
| GCM 篡改异常类型不同 | provider 统一映射 `AlgoIntegrityError("mac_mismatch")` |
| 基准受噪声影响 | N 次取 p50/p95；对称 256KB 跑 5 次；结果只作相对比较 |