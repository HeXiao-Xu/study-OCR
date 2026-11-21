# S4-9 RSA-OAEP+PSS 与 AES-GCM 对照实现 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-9（阶段 4 最后一张）。
> 目标：给 S6 性能实验提供"**同接口换实现**"的第二套算法，保证对比公平。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S4-9 行。

## 1. 目标

```
AlgoProvider（协议） ── SMProvider（SM2 + SM4-CBC + HMAC-SM3，默认）
                     └─ RSAProvider（RSA-OAEP-SHA256 + RSA-PSS-SHA256 + AES-256-GCM）
```

- `crypto/rsa_layer.py`：RSA-2048/3072 密钥生成、OAEP-SHA256 包会话密钥、PSS-MGF1-SHA256 签名；
- `crypto/aesgcm_layer.py`：AES-256-GCM（nonce 12B、tag 16B）；
- `crypto/provider.py`：`AlgoProvider` 协议 + 两套适配器 + `get_provider()`；
- `scripts/crypto_bench_compare.py`：两套实现的可比耗时（S6 用）。

**不改国密层与 `envelope.py`**（S5 才按 provider 选择、默认国密）。

## 2. 已拍板口径

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| Q1 | 接口统一表示 | `KeyMaterial{algo, public_key: bytes, private_key: bytes, key_fp}` | SM 用 hex 的 ASCII 字节、RSA 用 PEM 字节；对调用方不透明，接口才能完全相同 |
| Q2 | SM2 签名需要公钥（ZA） | 协议方法一律接收 `KeyMaterial`（不是只收私钥） | SM2-SM3 的 ZA 要公钥；只有这样两套签名接口才同名同参 |
| Q3 | 对称层接口 | `wrap_symmetric(cek, plaintext, *, aad) -> SymmetricPayload{nonce, ciphertext, tag}`；`unwrap_symmetric` 反向 | SM4：nonce=IV(16B)、tag=HMAC-SM3(32B)；AES-GCM：nonce 12B、tag 16B。两者都用"nonce/ciphertext/tag"三元组表示 |
| Q4 | 完整性异常归类 | 统一 `AlgoIntegrityError(reason)`；SM4 的 `IntegrityError.reason` 透传，GCM 的 `InvalidTag` 映射为 `mac_mismatch` | 参数化测试才能对两套实现断言同一个异常类型/原因 |
| Q5 | SMProvider.keygen | **不实现**（抛 `NotImplementedError`），SM 密钥由 `scripts/keygen.py`（OpenSSL，D-078）生成 | 不在 crypto 层再造 `_kg` 密钥生成；S5 用已生成密钥 |
| Q6 | RSA 参数 | 2048 或 3072；**OAEP 与 PSS 的 hash、MGF1 hash 全部显式 SHA-256** | 任务卡坑：不同库/版本默认值不同 |
| Q7 | RSA 密钥格式 | PKCS#8 私钥 PEM + SubjectPublicKeyInfo 公钥 PEM（未加密，测试/基准用） | `cryptography` 原生支持；生产私钥仍走 S4-5 的口令保护（本卡不接） |
| Q8 | 默认 provider | `get_provider("sm")`（默认）→ SMProvider；`"rsa"` → RSAProvider；未知 → `ValueError` | S5 配置选择，默认国密 |
| Q9 | 签名语义差异 | SM：`SM2-SM3`（ZA‖M）；RSA：`RSA-PSS` 对 M 直接签名（内部 SHA-256） | 两套都是"对 canonical 字节签名"，验证方用同一 provider；跨 provider 验签必须失败 |
| Q10 | 基准脚本 | `scripts/crypto_bench_compare.py`：N 次、p50/p95，输出 JSON + 表格；SM 密钥用 Docker OpenSSL 临时生成并清理 | S6 直接复用口径 |

## 3. `provider.py` API

```python
class AlgoIntegrityError(Exception):
    def __init__(self, reason: str) -> None: ...      # "mac_mismatch" / "bad_ciphertext" …

@dataclass(frozen=True)
class KeyMaterial:
    algo: str            # "SM2" / "RSA"
    public_key: bytes    # SM: 128 hex ASCII；RSA: 公钥 PEM
    private_key: bytes   # SM: 64 hex ASCII；RSA: PKCS#8 PEM
    key_fp: str          # 64 hex（SM: sha256(0x04‖X‖Y)；RSA: sha256(公钥 PEM)）

@dataclass(frozen=True)
class SymmetricPayload:
    nonce: bytes
    ciphertext: bytes
    tag: bytes

@runtime_checkable
class AlgoProvider(Protocol):
    name: str
    def keygen(self) -> KeyMaterial: ...
    def sign(self, payload: bytes, key: KeyMaterial) -> bytes: ...
    def verify(self, payload: bytes, signature: bytes, key: KeyMaterial) -> bool: ...
    def encrypt_key(self, cek: bytes, key: KeyMaterial) -> bytes: ...
    def decrypt_key(self, wrapped: bytes, key: KeyMaterial) -> bytes: ...
    def wrap_symmetric(self, cek: bytes, plaintext: bytes, *, aad: bytes = b"") -> SymmetricPayload: ...
    def unwrap_symmetric(self, cek: bytes, payload: SymmetricPayload, *, aad: bytes = b"") -> bytes: ...

def get_provider(name: str = "sm") -> AlgoProvider
```

`SMProvider`：`sign`/`verify` → `sm2.sign_bytes/verify_bytes`（DER 签名字节）；
`encrypt_key`/`decrypt_key` → `sm2.encrypt_bytes/decrypt_bytes`；`wrap/unwrap` → `sm4`（IV/HMAC）；
`keygen` → `NotImplementedError`；额外 `from_hex(private_hex, public_hex)` 构造 KeyMaterial（测试用）。

`RSAProvider(key_size=2048)`：`keygen` → `rsa_layer.generate_keypair`；`sign/verify` → PSS；
`encrypt_key/decrypt_key` → OAEP；`wrap/unwrap` → `aesgcm_layer`；`InvalidTag` → `AlgoIntegrityError("mac_mismatch")`。

## 4. 测试（`test_provider_rsa_gcm.py`，参数化两套实现）

| 用例 | 断言 |
|---|---|
| 签名往返 | 两套 provider：`verify(payload, sign(payload))` 为 True |
| 载荷/签名 1 bit 篡改 | `verify` False |
| 错密钥验签 | False |
| 跨 provider 验签 | False（SM 签名不能拿 RSA provider 验） |
| CEK 包裹往返 | `decrypt_key(encrypt_key(cek)) == cek`（16B） |
| 包裹密文篡改 / 错私钥 | 抛错（RSA 解包异常映射 `AlgoIntegrityError`/ValueError） |
| 对称往返 | `unwrap(wrap(plaintext)) == plaintext`（含中文与 64KB） |
| 对称篡改 | nonce/ciphertext/tag 任一 1 bit → `AlgoIntegrityError`；aad 变 → 同样失败 |
| 参数正确性 | SM4 payload：nonce 16B、tag 32B；GCM：nonce 12B、tag 16B |
| RSA 参数 | 2048/3072 生成成功；OAEP/PSS 显式 SHA256；>190B 的"CEK" 在 OAEP-2048 下抛错 |
| provider 工厂 | 默认 sm；`rsa`；未知 `ValueError` |
| 协议一致性 | `isinstance(SMProvider(), AlgoProvider)`、`isinstance(RSAProvider(), AlgoProvider)` |

验收命令：`pytest tests/test_crypto -q -k "rsa or gcm"` 全绿。

## 5. 基准脚本

`scripts/crypto_bench_compare.py`：对 `sign/verify/encrypt_key/decrypt_key/wrap_symmetric/unwrap_symmetric`
分别跑两套实现 N 次（默认 20；对称 256KB 跑 5 次），输出 JSON（含 p50/p95/ops）与可读表格。
SM 密钥用 `scripts/openssl.sh`（Docker）临时生成，`finally` 清理。**不改国密层参数**。

## 6. 验收

1. `pytest tests/test_crypto -q -k "rsa or gcm"` 全绿；
2. `scripts/crypto_bench_compare.py` 输出两套实现的可比耗时（记录到 PROGRESS）；
3. 全量单测/集成不回归；`alembic check` 零差异；
4. 文档收口：PROGRESS / DECISIONS（D-083）/ AGENTS 锚点 / HANDOFF（阶段 4 收口，下一步 S5-1）。

## 7. 已知限制

- 对照实现只用于**性能/对比实验**，不进生产默认链路（默认国密）；
- RSA 私钥在本卡是**未加密 PEM**（基准/测试用）；生产私钥保护仍走 S4-5（未接入 provider）；
- AES-GCM 的 nonce 由 `secrets` 每次新生成，但**没有**做 nonce 复用检测（GCM 的固有风险，论文写明）；
- SM 与 RSA 的安全强度不完全等价（SM2-256 ≈ RSA-3072；默认 2048 只作性能基线，基准脚本两者都给）。