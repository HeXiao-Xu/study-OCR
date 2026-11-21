# S4-3 SM4-CBC + HMAC-SM3 的 Encrypt-then-MAC 对称层 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-3（★关键）。
> 阶段 4 规则：只写 `backend/app/crypto/` 与 `backend/tests/test_crypto/`，**不碰业务接口**；
> 算法参数、密钥派生、MAC 覆盖范围在这里定死，S4-4 只调用不修改。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S4-3 行。

## 1. 目标

给"票据影像/字段 JSON"提供**可抗篡改的对称加密**，供 S4-4 数字信封直接复用：

```
key(16B) ──SM4-CBC(PKCS7, 随机 IV)──▶ ciphertext
mac_key = SM3(key ‖ "mac")
mac = HMAC-SM3(mac_key, aad ‖ IV ‖ ciphertext)      # Encrypt-then-MAC
```

- `crypto/sm/sm3.py`：补 `hmac_sm3(key, data) -> bytes(32)`（AGENTS §10.4 的对外清单里一直缺它）；
- `crypto/sm/sm4.py`：`derive_mac_key` / `encrypt` / `decrypt` / `Sm4Ciphertext` / `IntegrityError`。

范围外：信封封装与 SM2 包裹会话密钥（S4-4）、密钥库（S4-5）、AES-GCM/RSA 对照（S4-9）。

## 2. 已拍板口径

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| Q1 | IV 随机源 | `secrets.token_bytes(16)` | 任务卡写 `os.urandom`；AGENTS §6 统一要求 `secrets`（两者同为 CSPRNG，统一口径便于审计） |
| Q2 | 返回形态 | `Sm4Ciphertext(iv/ciphertext/mac: bytes)`，**不返回 hex** | 与 S4-1"hex 只进日志"一致；S4-4 直接 base64 落库。任务卡的 `{iv(hex), ciphertext(hex), mac(hex)}` 由调用方按需 `.hex()` |
| Q3 | CBC 与 PKCS7 谁做 | **本层实现链接与填充**，分组运算仍用 gmssl 的 `one_round` | gmssl `crypt_cbc` 无条件加/去填充，且 `pkcs7_unpadding = data[:-data[-1]]`（`gmssl/func.py:16`）**完全不校验**；无法满足"去填充失败抛 `IntegrityError`"。SM4 分组本体不重写（国标向量证明） |
| Q4 | MAC 覆盖范围 | `HMAC-SM3(mac_key, aad ‖ IV ‖ ciphertext)`；`mac_key = SM3(key ‖ "mac")` | Encrypt-then-MAC；IV 必须进 MAC（否则可翻转首块）；`aad` 留给 S4-4 把 `magic/cipher_algo/key_algo/recipient_cert_fp` 一起绑进去。加密 key 与 MAC key 不同（派生分离） |
| Q5 | 失败语义 | `IntegrityError(reason)`：`mac_mismatch` / `bad_padding` / `bad_ciphertext`；结构性长度错误（key/IV/MAC）抛 `ValueError` | 与 AGENTS §10.4 的失败归类对齐；**先验 MAC 再解密**，MAC 不过不产生任何明文 |
| Q6 | `hmac_sm3` 测试向量 | **无国标 HMAC-SM3 向量**（HMAC 不是国标定义内容）→ 用 OpenSSL 3（Node `createHmac('sm3')`）交叉向量 + RFC 2104 结构用例 | 如实写明出处，不冒充国标；结构正确性由"短 key/长 key/空输入"覆盖 |
| Q7 | 是否做 ECB 公开入口 | 否 | 业务只需要 CBC；ECB 只作为**内部**分组函数供国标向量测试 |
| Q8 | GCM | 不做 | gmssl 的 SM4 只有 ECB/CBC（任务卡明示的坑） |

## 3. `crypto/sm/sm3.py::hmac_sm3` 规格

```python
def hmac_sm3(key: bytes | bytearray | memoryview, data: bytes | bytearray | memoryview) -> bytes:
    """HMAC-SM3（RFC 2104；SM3 块长 64B、输出 32B）。返回 32 字节。"""
```

- `key` 超过 64B 先 `sm3_digest(key)`；不足补 0 到 64B；
- `ipad = 0x36 × 64`、`opad = 0x5C × 64`；`HMAC = SM3((K⊕opad) ‖ SM3((K⊕ipad) ‖ data))`；
- 入参只收字节类（`str` → `TypeError`），与 `sm3_digest` 同口径。

## 4. `crypto/sm/sm4.py` 规格

```python
BLOCK_SIZE: Final = 16
class IntegrityError(Exception):
    def __init__(self, reason: str) -> None: ...   # reason: "mac_mismatch" | "bad_padding" | "bad_ciphertext"

@dataclass(frozen=True)
class Sm4Ciphertext:
    iv: bytes
    ciphertext: bytes
    mac: bytes

def derive_mac_key(key: bytes) -> bytes                 # sm3_digest(key + b"mac")
def encrypt(key: bytes, plaintext: bytes, *, aad: bytes = b"") -> Sm4Ciphertext
def decrypt(key: bytes, sealed: Sm4Ciphertext, *, aad: bytes = b"") -> bytes
```

执行顺序（写死）：

1. `encrypt`：key 必须 16B（否则 `ValueError`）→ `iv = secrets.token_bytes(16)` →
   `ciphertext = SM4-CBC(PKCS7(plaintext), iv)` → `mac = hmac_sm3(mac_key, aad ‖ iv ‖ ciphertext)`；
2. `decrypt`：key 长度校验（`ValueError`）→ iv 必须 16B、mac 必须 32B（`ValueError`）→
   **先验 MAC**（`hmac.compare_digest`；不符直接 `IntegrityError("mac_mismatch")`，不碰密文）→
   密文长度必须是 16 的倍数且非空（否则 `IntegrityError("bad_ciphertext")`）→ CBC 解密 →
   **严格 PKCS7 去填充**（长度 1–16、每字节同值，否则 `IntegrityError("bad_padding")`）。

填充与链接细节：

- PKCS7 补 `n = 16 - len % 16`，`n ∈ [1,16]`（空明文补满一整块）；
- 去填充读最后 1 字节 `n`，要求 `1 ≤ n ≤ 16` 且末尾 `n` 字节全等于 `n`——不满足即 `bad_padding`；
- CBC 解密的链接在内存里逐块 XOR，明文只在通过 MAC 与填充校验后才返回。

## 5. 测试

`test_sm3.py` 追加（`hmac_sm3`）：

| 用例 | 断言 |
|---|---|
| OpenSSL 交叉向量 ① | `key=b"key"`、`data=b"The quick brown fox jumps over the lazy dog"` → `bd4a3407…ebd398` |
| ② 长 key（>64B，先哈希） | `key=b"k"*100`、`data=b"data"` → `d2e31919…96fedfe8` |
| ③ 空 key + 空数据 | → `0d23f72b…915261` |
| ④ 业务形态 | `key=b"secret"`、`data=b"canonical-payload"` → `34be4603…dad064` |
| 返回形态 | `bytes`、长度 32；与 `sm3_digest(key+data)` 不同 |
| 入参 | `bytearray`/`memoryview` 可用；`str` 抛 `TypeError` |

`test_sm4.py`：

| 用例 | 断言 |
|---|---|
| **GB/T 32907-2016 国标向量**（ECB 单块） | key=PT=`0123456789abcdeffedcba9876543210` → `681edf34d206965e86b3e94f536e4246`（测内部 `_encrypt_block`） |
| CBC 与 OpenSSL 对照（固定 IV） | `key=0123…3210`、`iv=000102…0e0f`、`"hello 国密 world"` → `61e48c1e…5c`（Node `createCipheriv('sm4-cbc')` 同值） |
| 往返 | 长度 0/1/15/16/17/100/65536 与中文载荷 |
| 同明文两次 | 密文与 IV 都不同（随机 IV） |
| 篡改 | 改密文 1 bit / 改 IV / 改 MAC / 错误 key → `IntegrityError("mac_mismatch")` |
| 截断 | 去掉一个分组 → `IntegrityError`（mac_mismatch） |
| 坏填充 | 构造 MAC 合法的坏填充密文 → `IntegrityError("bad_padding")` |
| aad | 加密/解密同一 aad 通过；aad 不同 → `mac_mismatch` |
| 结构错误 | key≠16B / iv≠16B / mac≠32B → `ValueError` |
| 失败不泄露明文 | `IntegrityError` 的 `reason` 只有归类字符串；异常里无明文 |

## 6. 验收

1. `pytest backend/tests/test_crypto -q` 全绿（S4-1/S4-2 的 105 条不回归）；
2. SM4 国标向量通过；CBC 与 Node/OpenSSL 逐字节一致（命令行交叉验证，记录到 PROGRESS）；
3. 全量单测无回归；`alembic check` 零差异（本卡无迁移、无接口）；
4. 文档收口：PROGRESS / DECISIONS（D-076）/ AGENTS §10.8 / HANDOFF。

## 7. 已知限制

- gmssl 是纯 Python，SM4 吞吐低（S6-3 会测 p50/p95；论文如实写"原型级性能"）；
- CBC 无认证加密的并行性优势，MAC 单独一层（Encrypt-then-MAC 的标准取舍）；
- `mac_key = SM3(key‖"mac")` 是简易 KDF；严格场景应用 HKDF（S4-9 对照实现里可比较）；
- 不做常量时间保证（纯 Python），侧信道分析留 S6-4。