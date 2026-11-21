# S4-5 密钥生成与私钥口令保护 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-5（★关键）。
> 阶段 4 规则：只写 `backend/app/crypto/`、`backend/tests/test_crypto/` 与 `scripts/`；
> 私钥格式、口令参数在这里定死，S5 只调用不修改。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S4-5 行。

## 1. 目标

```
openssl genpkey -algorithm SM2 ──▶ PKCS#8 PEM（真私钥）
        │  Python 只取 DER
        ▼
PBKDF2-HMAC-SHA256(口令, 16B 盐, ≥200k) ──▶ 32B KEK ──▶ AES-256-GCM(PKCS#8 DER)
        ▼
secrets/keys/<name>/sign.p8.enc  {algo, curve, kdf, iterations, salt_b64, nonce_b64, ciphertext_b64, key_fp}
        │  解锁
        ▼
(d_hex, public_hex) ──▶ 交给 S4-2 签名 / S4-4 验签
```

- `backend/app/crypto/keystore.py`：包装/解包 + PKCS#8 解析 + 指纹（**不生成密钥**，生成交给 OpenSSL）；
- `backend/app/crypto/sm/sm2.py`：补 `public_key_from_private(d_hex)`（dG 推导公钥，与 OpenSSL 交叉核对）；
- `scripts/keygen.py`：命令行"生成 → 加锁 → 解锁 → 签名自检"，私钥与口令都不进日志。

## 2. 已拍板口径

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| Q1 | 密钥生成方式 | **OpenSSL 3.5 CLI（`scripts/openssl.sh`，Docker 内）**，不用 gmssl `_kg` 造密钥 | 任务卡要求；`_kg` 是库内部 API，任务卡明确只作应急。`_kg` 在本卡只用于**从 d 推导公钥**并与 `openssl pkey -pubout` 交叉核对（同一套曲线参数） |
| Q2 | 密钥文件格式 | JSON：`{algo:"SM2", curve:"sm2p256v1", kdf:"PBKDF2-HMAC-SHA256", iterations, salt_b64, nonce_b64, ciphertext_b64, key_fp}`，后缀 `.p8.enc` | 任务卡字段 + algo/curve（`key_records.algo` 可对账）；二进制字段 base64；**明文 PKCS#8 不落盘**（临时 PEM 用完即删） |
| Q3 | 口令参数 | PBKDF2-HMAC-SHA256，iterations **默认且下限 200000**，salt 16B（`secrets`），KEK 32B；AES-256-GCM nonce 12B | 任务卡下限；迭代次数写进文件并进 AAD，**调低即解密失败** |
| Q4 | GCM 的 AAD | `canonical_json({algo, curve, kdf, iterations, key_fp})` | 防"把 iterations 改成 1"或换算法/换指纹的降级攻击；复用 S4-1 的规范化 |
| Q5 | `key_fp` | `SHA-256(0x04‖X‖Y)` 的 64 位小写 hex | 标识公钥（与证书指纹区分）；由私钥可复算，便于核对记录 |
| Q6 | 口令策略 | 长度 ≥ **8** 字符；空/过短 → `ValueError` | 短口令 + 200k 迭代仍弱；毕业设计演示用 8+ 位即可 |
| Q7 | 解锁失败文案 | 统一 `KeystoreError("口令错误")`：**不区分**错口令 / 密文被改 / 元数据被改 | 防 padding/tag oracle；任务卡要求"统一提示口令错误" |
| Q8 | 是否落库 | **本卡不写库**。SCHEMA `key_records` 只有 `storage_path`（无 `key_fp`/`status` 列），S5 按 SCHEMA 登记路径 | AGENTS §9：SCHEMA 优先于任务卡字段清单；`key_fp` 存在加密文件里 |
| Q9 | 解锁产物 | `load_keypair()` → `KeyPair{private_key_hex, public_key_hex, key_fp}`；私钥只以 hex 在内存 | S4-2 的 `sign_bytes` 正好吃 d_hex + pub_hex |
| Q10 | PKCS#8 解析 | 用 pycryptodomex 的 `DerSequence`/`DerOctetString` **解码原语**逐层取 `d`；不手写 ASN.1 编解码 | AGENTS §2.1；`cryptography` 不支持 SM2 键 |
| Q11 | 临时文件 | 生成时 PEM 写在 `secrets/keys/.tmp-<uuid>.pem`，`finally` 必删；密钥目录在 `.gitignore` 内 | AGENTS §4.3 明文只在内存/临时目录 |

## 3. 模块 API

```python
# backend/app/crypto/keystore.py
DEFAULT_ITERATIONS = 200_000
MIN_ITERATIONS = 200_000
MIN_PASSPHRASE_CHARS = 8

class KeystoreError(Exception): ...           # 解锁失败：统一 "口令错误"

class EncryptedPrivateKey(BaseModel):          # frozen / extra=forbid
    algo: Literal["SM2"] = "SM2"
    curve: Literal["sm2p256v1"] = "sm2p256v1"
    kdf: Literal["PBKDF2-HMAC-SHA256"] = "PBKDF2-HMAC-SHA256"
    iterations: int                             # >= MIN_ITERATIONS（模型校验）
    salt_b64: str; nonce_b64: str; ciphertext_b64: str
    key_fp: str                                 # ^[0-9a-f]{64}$

@dataclass(frozen=True)
class KeyPair:
    private_key_hex: str
    public_key_hex: str
    key_fp: str

def derive_kek(passphrase: str, salt: bytes, iterations: int = DEFAULT_ITERATIONS) -> bytes
def key_fingerprint(public_key_hex: str) -> str
def private_key_hex_from_pkcs8(pkcs8_der: bytes) -> str
def wrap_private_key(pkcs8_der: bytes, passphrase: str, *, iterations: int = DEFAULT_ITERATIONS) -> EncryptedPrivateKey
def unwrap_private_key(record: EncryptedPrivateKey, passphrase: str) -> bytes
def load_keypair(record: EncryptedPrivateKey, passphrase: str) -> KeyPair

# backend/app/crypto/sm/sm2.py
def public_key_from_private(private_key_hex: str) -> str   # dG，128 hex，不带 04
```

## 4. `scripts/keygen.py`

```bash
cd /e/BiSheXZY/study-OCR
STUDYOCR_KEY_PASSPHRASE='demo-pass-1234' .venv/Scripts/python.exe \
  scripts/keygen.py --name alice --role submitter
```

流程：`scripts/openssl.sh genpkey -algorithm SM2` + `pkey -pubout -outform DER` → 读 PEM/DER →
`wrap_private_key` → 写 `secrets/keys/alice/sign.p8.enc` 与 `alice/sign.pub.pem` →
`load_keypair` 解锁 → 用解锁私钥对固定消息签名并验签（自检）→ 打印
`{name, role, algo, key_fp, public_key_hex, path, iterations}`。**绝不打印口令与 d**；
临时 PEM 在 `finally` 删除；口令从环境变量或 `getpass` 读，不接受命令行参数。

## 5. 测试（`test_keystore.py`，默认跑，不需要 Docker）

| # | 用例 | 断言 |
|---|---|---|
| 1 | wrap → unwrap | 返回的 DER 与输入逐字节相同 |
| 2 | `load_keypair` | d_hex 合法、公钥 == `public_key_from_private(d)`、`key_fp` 与记录一致 |
| 3 | 解锁后签名 | `sign_bytes` 后 `verify_bytes` 为 True（S4-2 联通） |
| 4 | 错误口令 | `KeystoreError("口令错误")` |
| 5 | 篡改密文 1 字节 | `KeystoreError("口令错误")` |
| 6 | 篡改盐 / nonce | `KeystoreError` |
| 7 | 篡改 iterations（model_copy 绕过校验） | `KeystoreError`（AAD 失配） |
| 8 | 空口令 / 7 字符口令 | `ValueError`（wrap 与 unwrap 都拒绝） |
| 9 | iterations < 200000 调 wrap | `ValueError`；模型构造也拒绝 |
| 10 | PKCS#8 解析：非法 DER / 非 32B 标量 / 版本不对 | `ValueError("不是有效的 PKCS#8")` |
| 11 | 模型校验 | frozen、extra=forbid、`key_fp` 必须 64 hex |
| 12 | 指纹 | 64 hex；不同公钥不同指纹；公钥 130/128 hex 两种写法同指纹 |
| 13 | 两条不同 d 的密钥 | 包装后互不通用（错钥解锁 → 口令错误） |

> 测试用**程序构造**的最小 PKCS#8（pycryptodomex DER 原语 + 固定 d），不把 PEM 私钥字面量写进仓库；
> 真实 OpenSSL 产出的 PKCS#8 由 `scripts/keygen.py` 的验收跑覆盖。

## 6. 验收

1. `pytest backend/tests/test_crypto -q` 全绿（S4-1~S4-4 的 181 条不回归）；
2. `scripts/keygen.py` 生成 → 加锁 → 解锁 → 签名自检 全链路跑通（Docker 在跑）；
3. **用 `scripts/openssl.sh pkeyutl -verify -rawin -digest sm3` 验证我们签的名**（独立实现互操作）；
4. `grep -rn "PRIVATE KEY" backend/ docs/` 无命中（`secrets/` 与 `scripts/` 不在此范围）；
5. 日志/输出里搜不到口令与 d；临时 PEM 不残留；
6. 全量单测无回归；`alembic check` 零差异（无迁移）。

## 7. 已知限制

- 本卡不做密钥轮换/吊销（`key_records.revoked_at` 属 S5 管理动作）；
- 口令由环境变量或交互输入，**没有 HSM**（论文"不足"如实写）；
- PKCS#8 解析只接受 SM2/EC 结构，不支持的算法给明确 `ValueError`；
- 私钥文件是"口令加密 + 文件系统权限"的单机保护，防的是"拿到库/备份"而非"拿到主机"。`