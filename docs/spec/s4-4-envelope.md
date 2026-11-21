# S4-4 数字信封（SM2 包裹会话密钥） — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-4（★关键）。
> 阶段 4 规则：只写 `backend/app/crypto/` 与 `backend/tests/test_crypto/`，**不碰业务接口**。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S4-4 行。

## 1. 目标

```
CEK = secrets.token_bytes(16)                     # 每次信封新生成
ct  = SM4-CBC(CEK, PKCS7(plaintext), iv, aad=header‖wrapped_key, trailer=cert_fp)
mac = HMAC-SM3(SM3(CEK‖"mac"), header ‖ wrapped_key ‖ IV ‖ ct ‖ cert_fp)
wrapped_key = SM2-C1C3C2(财务公钥, CEK)            # 64B C1 ‖ 32B C3 ‖ 16B C2 = 112B
```

- `crypto/envelope.py`：`seal` / `open_sealed` / `Envelope` / `EnvelopeError`；
- `crypto/sm/sm2.py` 补 **SM2 加解密**（`encrypt_bytes` / `decrypt_bytes`，C1C3C2、无 04 前缀、只收 1–32B）；
- 为满足"2MB 影像 1 秒内"，`sm3.py` 与 `sm4.py` 增加 **OpenSSL 后端**（见 §3）。

## 2. 已拍板口径

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| Q1 | 信封字段 | `Envelope{cipher_mode="SM4-CBC", key_algo="SM2", iv_b64, ciphertext_b64, mac_b64, wrapped_key_b64, recipient_cert_fp, payload_sha256, payload_version="v1", created_at}` | 任务卡记录的字段 + `payload_version`（进 MAC 头）；SCHEMA `envelopes` 的列名一一可映射 |
| Q2 | SM2 加密实现 | **不用** `CryptSM2.encrypt`（它内部取 K 用 `func.random_hex`，K 可预测且无法注入）；用 gmssl 的 `_kg` + `sm3_kdf` + `sm3_digest` 按 GB/T 32918.4 组合 C1C3C2，K 走 `secrets` | AGENTS §6 禁止 `func.random_hex`；组合用库的点运算与 KDF，不重写曲线/SM3 |
| Q3 | C1 前缀 | **不带 `0x04`**（与 gmssl 一致、与 SCHEMA 注释一致：`wrapped_key` 约 112B = 64+32+16） | AGENTS §6：跨实现转换只在这里做；本卡不做自动剥离（C1 首字节本就可能是 0x04，自动剥离会误伤） |
| Q4 | 解密是否校验 C3 | **不比**（与 gmssl 行为一致；AGENTS §10.4："不依赖 SM2 自带 C3"） | wrapped_key 的任何篡改都会让 CEK 变→MAC 变；C3 段被改则装不进 MAC？→ **见 Q5** |
| Q5 | MAC 覆盖范围 | `header ‖ wrapped_key ‖ IV ‖ ciphertext ‖ recipient_cert_fp`（比 SCHEMA 多绑 `wrapped_key`） | 不依赖 C3 就必须把 wrapped_key 本身绑进 MAC，否则改 C3 段无人发现。仍是"所有字段都被绑定"的严格超集 |
| Q6 | `payload_sha256` | 明文字节（打包后的影像/JSON）的 **SHA-256**，hex | 任务卡/SCHEMA 字段名就是 sha256；与 SM3 摘要分开（SM3 用于签名域） |
| Q7 | `recipient_cert_fp` | 64 位小写 hex（证书指纹 sha256） | S4-6 从证书算；本卡只登记 + 进 MAC |
| Q8 | 失败归类 | `EnvelopeError(reason)`：`bad_envelope`（结构/base64/长度）、`mac_mismatch`、`bad_padding`、`digest_mismatch`；S4-6 再加 `cert_invalid` | 与 AGENTS §10.4 归类对齐；sm4 的 `IntegrityError.reason` 直接透传 |
| Q9 | 性能路线 | **SM4 走 `cryptography`（OpenSSL）**、**SM3/HMAC 走 `hashlib`（OpenSSL）**，gmssl 保留为回退 | 实测：纯 Python 2MB SM4 往返 28.1s、SM3 7.5s；OpenSSL 0.03s / 0.008s。任务卡要求 <1s，不换就是造假 |
| Q10 | 证书校验/验签 | 本卡**不做** | `certs.py` 属 S4-6；签名属 S5 的业务链。`open_sealed` 只做：解 CEK → 验 MAC 解密 → sha256 |

## 3. 性能改造（Q9 的落地，D-077）

`sm3.py`：

- `try hashlib.new("sm3")`：可用则走 OpenSSL（C 实现，2MB 0.008s）；不可用回退 gmssl（纯 Python，
  保留"gmssl 唯一入口 + 入参副本"的原有处理）；
- `hmac_sm3`：OpenSSL 可用时用 `hmac.new(key, data, "sm3")`；否则回退手写 RFC 2104。
  两条路径必须有同一组向量测试（monkeypatch 模块标志切回退分支）。

`sm4.py`：

- 分组与 CBC 改由 `cryptography` 的 `Cipher(algorithms.SM4(...), modes.CBC/ECB)` 完成
  （仍保留 `_encrypt_block` / `_cbc_encrypt` / `_cbc_decrypt` 三个内部函数供向量测试）；
- PKCS7 与 EtM 逻辑**不变**（仍自实现填充校验）；
- 新增 `trailer` 参数：`mac = HMAC-SM3(mac_key, aad ‖ IV ‖ 密文 ‖ trailer)`，
  让信封把 `recipient_cert_fp` 放在密文之后（SCHEMA 的覆盖顺序）。

> 依赖：`cryptography==50.0.2` 已在环境与 `requirements.lock.txt` 中；本卡把它**补进
> `requirements.txt`**（AGENTS §2 已列为项目依赖，S4-5 还要用它做 AES-GCM/PBKDF2）。

## 4. SM2 加解密规格（`crypto/sm/sm2.py` 追加）

```python
def encrypt_bytes(plaintext: bytes, *, public_key_hex: str) -> bytes   # C1(64)‖C3(32)‖C2(n)
def decrypt_bytes(ciphertext: bytes, *, private_key_hex: str) -> bytes
```

- 明文长度必须 **1–32 字节**（只用于包裹 16B CEK；更长直接 `ValueError`——SM2 不用于大块数据）；
- `encrypt_bytes`：`k = secrets` 拒绝采样 → `C1 = kG` → `shared = kP` → `t = KDF(x2‖y2, len)` →
  `C2 = M ⊕ t` → `C3 = SM3(x2‖M‖y2)`；KDF 全零时换 K 重试（≤8 次）；
- `decrypt_bytes`：长度必须 `97–128`；C1 必须在曲线上（否则 `ValueError`）；
  `shared = d·C1` → `M = C2 ⊕ KDF(...)`；**不比对 C3**（Q4）；
- 公钥/私钥校验复用 S4-2 的 `_normalize_public_key` / `_validate_private_key`。

## 5. `crypto/envelope.py` 规格

```python
class EnvelopeError(Exception):
    def __init__(self, reason: str) -> None: ...   # bad_envelope | mac_mismatch | bad_padding | digest_mismatch

class Envelope(BaseModel):   # frozen / extra=forbid / created_at 必须带时区
    cipher_mode: Literal["SM4-CBC"] = "SM4-CBC"
    key_algo: Literal["SM2"] = "SM2"
    iv_b64: str; ciphertext_b64: str; mac_b64: str; wrapped_key_b64: str
    recipient_cert_fp: str          # ^[0-9a-f]{64}$
    payload_sha256: str             # ^[0-9a-f]{64}$
    payload_version: str = "v1"
    created_at: datetime

def seal(plaintext, *, recipient_public_key_hex, recipient_cert_fp,
         payload_version="v1", now=None) -> Envelope
def open_sealed(envelope: Envelope, *, private_key_hex: str) -> bytes
```

- `header = b"study-ocr-envelope" + b"\x00" + cipher_mode + b"\x00" + key_algo + b"\x00" + payload_version`
  （固定顺序的 UTF-8 字节；改任一项都会让 MAC 失配）；
- `seal`：CEK=`secrets.token_bytes(16)` → `sm4.encrypt(CEK, plaintext, aad=header‖wrapped_key, trailer=cert_fp)`
  （注：实现顺序上先 SM2 包 CEK 得到 wrapped_key，再算 SM4/MAC）→ 组装 Envelope（各字段 base64）；
- `open_sealed`：base64 解码（失败 → `bad_envelope`）→ `sm2.decrypt_bytes`（`ValueError` → `bad_envelope`）→
  `sm4.decrypt`（`IntegrityError.reason` 透传为 `EnvelopeError`）→ `sha256(明文)` 比对（不符 → `digest_mismatch`）；
- 顺序按任务卡：**解 CEK → 验 MAC 解密 → 摘要比对**。AGENTS §10.4 写的"验 MAC → 解 CEK"
  在物理上不可能（MAC key 由 CEK 派生），本卡按任务卡执行并在 `DECISIONS` 说明。

## 6. 测试（`test_sm2.py` 追加 + 新 `test_envelope.py` + `test_sm3/test_sm4` 追加）

| 文件 | 用例 |
|---|---|
| `test_sm3.py` | OpenSSL 与 gmssl 回退两条路径同向量（monkeypatch `_OPENSSL_SM3`）；`hmac_sm3` 同样覆盖两条路径 |
| `test_sm4.py` | `trailer` 参与 MAC（换 trailer → `mac_mismatch`）；原有国标/交叉/篡改用例不回归 |
| `test_sm2.py` | SM2 加解密往返（16B CEK、1B、32B）；**>32B → ValueError**；同明文两次密文不同；篡改 C1/C2 → 解出错误值或异常；C1 不在曲线 → ValueError；与 gmssl `encrypt/decrypt` 互操作（gmssl 能解我们的密文，我们能解 gmssl 的密文） |
| `test_envelope.py` | 往返（小载荷 + 中文 + 2MB）；同明文两次信封全字段不同；篡改 ciphertext/iv/mac/**wrapped_key（含 C3 段）**/cert_fp/payload_sha256 都有明确异常；错私钥；错误 base64/长度 → `bad_envelope`；`created_at` naive 拒绝；模型 frozen/extra=forbid；**2MB seal+open < 1s** |

## 7. 验收

1. `pytest backend/tests/test_crypto -q` 全绿（S4-1~S4-3 的 146 条不回归）；
2. **2MB seal→open 实测 < 1s**（OpenSSL 后端；实测值写进 PROGRESS）；
3. 篡改 `wrapped_key_b64` / `ciphertext_b64` 抛明确异常；
4. 全量单测无回归；`alembic check` 零差异（无迁移、无接口）；
5. 文档收口：PROGRESS / DECISIONS（D-077）/ AGENTS / HANDOFF。

## 8. 已知限制

- SM2 解密不校验 C3（Q4），wrapped_key 的完整性靠 MAC 绑定（Q5）；
- 信封只到"字节 → Envelope 记录"，大影像存 MinIO 的对象名（`ciphertext_storage_key`）属 S5；
- 证书解析/吊销属 S4-6，`recipient_cert_fp` 目前由调用方传入；
- 不做常量时间实现（纯 Python 分支），侧信道分析留 S6-4。