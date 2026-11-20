# S4-2 SM2 签名与验签（防双重哈希） — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-2（★关键）。
> 阶段 4 规则：只写 `backend/app/crypto/` 与 `backend/tests/test_crypto/`，**不碰业务接口**；
> 算法参数、签名编码、记录格式在这里定死，S5 只调用不修改。
> 状态：**已实现（2026-11-20）**，实测见 `PROGRESS.md` S4-2 行。

## 1. 目标

把 S4-1 的"唯一字节串 + 摘要"接到国标签名算法上，形成 S5 可直接调用的签名/验签闭环：

```
业务 dict ─canonical_json─▶ bytes ─(sign_bytes: SM2-SM3, 含 ZA)─▶ signature(DER)
                                   └─sm3_digest─▶ digest_hex（审计/快对，不是验签依据）
```

- `crypto/sm/sm2.py`：SM2 签名/验签的唯一入口（`sign_bytes` / `verify_bytes`）；
- `crypto/signature.py`：签名记录（`SignatureRecord`）+ `sign_canonical` / `verify_canonical` /
  `verify_record`，把"规范化 → 摘要 → 签名 → 记录"和"记录 → 验签 → 分类结论"固化成一条链路。

范围外：SM4/信封（S4-3/S4-4）、密钥生成与私钥保护（S4-5）、证书与吊销（S4-6）、
审计哈希链（S4-7）、RSA-PSS 对照（S4-9）。**本卡不写库、不加表、不碰接口。**

## 2. 与任务卡 / AGENTS §6 的取舍（必须先看这一段）

任务卡 S4-2 的提示词写的是"对外只暴露 `sign_bytes(digest32) -> hex`，函数内不再做任何哈希"，
而 AGENTS §6 的硬约束是"签名验签**统一用** `sign_with_sm3` / `verify_with_sm3`；禁止与
`sign(data, K)` 混用"。两者不能同时满足（前者要求把 32B 摘要当 `e` 直接签，等于放弃国标 ZA 绑定）。

**裁决：遵守 AGENTS §6，走标准 SM2-SM3。** 理由：

1. **国标要求**：GB/T 32918.2 的签名值 `e = SM3(ZA ‖ M)`，`ZA` 把用户 ID、曲线参数、公钥绑在一起。
   直接对 32 字节摘要做裸签名会丢掉 ZA 绑定，**不是国标 SM2 签名**，答辩必被追问；
2. **可验证性**：标准路线能用 GB/T 32918.5 附录 A.1 的公开向量逐位对账（见 §5），
   裸签名路线只能自己签自己验（循环论证）；
3. **AGENTS 是宪法**（§1「与任务卡冲突时以本文件的硬约束为准」）。

因此 `sign_bytes` 的入参语义改为**待签字节（= `canonical_json` 的输出）**，不是摘要：

| 任务卡原语义 | 本卡实现 | 说明 |
|---|---|---|
| `sign_bytes(digest32) -> hex` | `sign_bytes(data: bytes) -> str` | 函数名保留；`data` 是规范化载荷，SM3/Z A 由 gmssl 内部按国标做一次 |
| "函数内不再做任何哈希" | 封装层不做哈希，但 `sign_with_sm3` 内部按国标做一次 `SM3(ZA‖M)` | 这正是"只能选一种"的那一种；**绝不二次摘要** |
| 摘要独立可用 | `digest_hex = SM3(canonical)` 仍由 S4-1 产生并入库 | 按 SCHEMA：**只用于展示与快速比对，不是验签依据** |

> 双重哈希陷阱的处理：整个仓库只有 `sign_bytes` 一个签名入口，它内部只做一次 SM3；
> 测试把"把摘要当消息再签"的错误路径固化成用例（§5 第 8 条），验签必须失败。

## 3. `crypto/sm/sm2.py` 精确规格

```python
DEFAULT_USER_ID: Final = b"1234567812345678"   # 国标默认 ID（ENTLA=0x0080）

def sign_bytes(
    data: bytes | bytearray | memoryview,
    *,
    private_key_hex: str,
    public_key_hex: str,
    k_hex: str | None = None,        # 仅供国标向量/确定性测试；生产永远不传
) -> str:                            # 返回 DER(SEQUENCE{r,s}) 的十六进制

def verify_bytes(
    data: bytes | bytearray | memoryview,
    signature_hex: str,
    *,
    public_key_hex: str,
) -> bool:
```

**密钥格式**（本卡定死，S4-5/S4-6 沿用）：

- 私钥：64 位十六进制（32 字节），标量 `d ∈ [1, n-2]`；不合法 → `ValueError`；
- 公钥：**128 位十六进制 `x‖y`**，也接受 130 位带 `04` 前缀的未压缩点（入口统一归一化）；
  必须满足曲线方程 `y² = x³ + ax + b (mod p)`，否则 `ValueError`；
- 签名值：**DER ASN.1 `SEQUENCE{ r INTEGER, s INTEGER }` 的 hex**（70–72 字节），
  与 SCHEMA `signatures.signature_b64`（base64，约 70–72 字节）对应；不用裸 `r‖s`。

**必须处理的 gmssl 0.3.2.2 行为**（全部有对应测试）：

| # | gmssl 行为 | 本卡处理 |
|---|---|---|
| G1 | `mode` 默认 **0**（C1C2C3），docstring 却写 default is 1 | 显式 `mode=1`（本卡签名不用 mode，但按 AGENTS 统一约束） |
| G2 | `public_key.lstrip("04")` **按字符集剥离**，不是去前缀：x 以 `04` 开头（约 1/256 概率）的公钥会被剥坏 | 入口归一化成 128 hex，构造后再**强制覆盖** `sm2.public_key`；用一次性生成的真密钥对（`d=7F3A…6146`，PA 以 `04` 开头）回归 |
| G3 | `sign()` 遇到 `r=0`、`r+k=n`、`s=0` 返回 `None`，**不重试** | 封装层换新 K 重试，最多 8 次；固定 K 失败直接 `RuntimeError` |
| G4 | `sign_with_sm3(data, None)` 用 `func.random_hex`（基于 `random`，K 可预测） | **永远显式传 K**：`secrets.token_hex(32)` + 拒绝采样（`1 ≤ K ≤ n-1`），禁止 `random` |
| G5 | `verify()` 不校验 `r, s ∈ [1, n-1]`（`s+n` 的可塑变体能验过） | 验签前用 ASN.1 解出 r,s 做范围校验，越界直接 `False` |
| G6 | `verify()` 对畸形签名会抛 `ValueError`/`TypeError`，`t=0` 时返回 `0`（int 不是 bool） | 封装层统一 `bool`；畸形一律 `False`（验签不是"抛异常"的语义） |
| G7 | `_sm3_z` 用默认 ID `1234567812345678` | 作为契约常量写死并导出 `DEFAULT_USER_ID`；将来换 ID 必须同时改签名/验签并记决策 |

**随机数**：只允许 `secrets`（AGENTS §6）。K 用拒绝采样保证落在 `[1, n-1]`；
K **不进日志、不进异常信息、不进记录**。

## 4. `crypto/signature.py` 精确规格

```python
class VerifyResult(StrEnum):        # 与 SCHEMA signatures.verify_result 的取值对齐
    OK = "ok"
    DIGEST_MISMATCH = "digest_mismatch"
    BAD_SIGNATURE = "bad_signature"
    # cert_invalid 由 S4-6 的证书层产生，本卡不返回

class SignatureRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    algo: Literal["SM2-SM3"] = "SM2-SM3"
    digest_algo: Literal["SM3"] = "SM3"
    digest_hex: str                       # SM3(canonical_bytes)，64 位小写 hex
    signature_b64: str                    # base64(DER(r,s))
    canonical_payload_base64: str         # base64(canonical bytes)
    payload_version: str                  # 签名域版本，如 "v1"
    cert_id: int | None = None            # S4-6 之前可为 None，S5 落库前必须补齐
    signed_at: datetime                   # 必须带时区（统一 UTC）

def sign_canonical(
    obj: object, *, private_key_hex: str, public_key_hex: str,
    cert_id: int | None = None, payload_version: str = "v1",
    now: datetime | None = None,
) -> SignatureRecord

def verify_record(record: SignatureRecord, *, public_key_hex: str) -> VerifyResult

def verify_canonical(obj: object, record: SignatureRecord, *, public_key_hex: str) -> VerifyResult
```

语义（写死，S5 只调用）：

1. `sign_canonical`：`canonical_json(obj)` → `digest_hex` → `sign_bytes(payload)`；
   记录里**存签名当时的原文字节**（base64）——AGENTS §10.4：验签用它重算，不重新拼装；
2. `verify_record`：先比对 `digest_hex` 与 base64 载荷的 SM3（不符 → `digest_mismatch`），
   再验签（不符 → `bad_signature`），返回 `VerifyResult`；
3. `verify_canonical`：先 `canonical_json(obj)` 与记录里的载荷**逐字节相等**（不等 → `digest_mismatch`），
   否则再走 `verify_record` 的流程；
4. 记录不可变（`frozen=True`）；`extra="forbid"` 防止上层塞未知字段；
   `signed_at` 为 naive datetime → 校验失败（时区必须显式）；
5. `cert_id` 本卡只登记不校验（证书链/吊销属 S4-6）。

## 5. 测试（`backend/tests/test_crypto/test_sm2.py` + `test_signature.py`）

`test_sm2.py`（默认跑，不打标记）：

| # | 用例 | 断言 |
|---|---|---|
| 1 | **GB/T 32918.5 附录 A.1 标准向量**（推荐曲线 sm2p256v1，ID `1234567812345678`，`M="message digest"`，`dA=3945208F…C5B8`，`k=59276E27…BC21`） | 签名解出的 `r=F5A03B06…20B3`、`s=B1B6AA29…C1AA`；`verify_bytes` 为 True |
| 2 | 往返 | 签名 → 验签 True（`k` 随机） |
| 3 | 1 bit 篡改消息 | 验签 False |
| 4 | 1 字符篡改签名（DER 内某个 hex 位） | 验签 False（不抛异常） |
| 5 | 错误公钥验签 | False |
| 6 | 同一明文连签两次 | 签名**不同**（K 来自 `secrets`） |
| 7 | 固定 K 连签两次 | 签名**相同**（证明 `k_hex` 确实被采纳） |
| 8 | **双重哈希陷阱**：把 `sm3_digest(payload)` 当"消息"再签 | 对摘要能验过、对原 payload 必须验不过 |
| 9 | `04` 前缀的 x（G2 的 lstrip 缺陷回归） | 签名/验签都正确 |
| 10 | 公钥三种写法（128 hex / 130 hex 带 `04` / 大小写混合） | 都能验签 |
| 11 | 畸形输入 | 非法私钥/公钥/签名长度/非 hex → `ValueError`（verify 路径返回 False） |
| 12 | 可塑性：`s+n` 的变体签名 | `False`（G5） |
| 13 | 不在曲线上的公钥 / 私钥越界（0、n-1） | `ValueError` |
| 14 | 空消息 `b""`、长消息（64KB） | 往返正确 |

`test_signature.py`：

| # | 用例 | 断言 |
|---|---|---|
| 1 | `sign_canonical` 记录字段 | `algo/digest_algo/digest_hex/canonical_payload_base64/signature_b64/signed_at/payload_version` 全对；`digest_hex == SM3(canonical)` |
| 2 | `verify_canonical` 往返 | `OK` |
| 3 | 载荷被改（同字段换值） | `digest_mismatch` |
| 4 | 载荷 + `digest_hex` 一起被改 | `bad_signature` |
| 5 | 签名被改 / 错误公钥 | `bad_signature` |
| 6 | `canonical_payload_base64` 非法 base64 | `digest_mismatch`（无法确认载荷） |
| 7 | `signature_b64` 非法 base64 | `bad_signature` |
| 8 | 模型校验 | 未知字段 → 拒绝；naive `signed_at` → 拒绝；空 `payload_version` → 拒绝 |
| 9 | 记录不可变 | 赋值抛异常 |
| 10 | `canonical_json` 拒绝的类型（float/Decimal） | `sign_canonical` 抛 `CanonicalJSONError`（复用 S4-1） |

## 6. 验收

1. `pytest backend/tests/test_crypto/test_sm2.py -q` 全绿，且第 1 条用**GB/T 公开向量**；
2. `pytest backend/tests/test_crypto -q` 全绿（S4-1 的 54 条不得回归）；
3. 用独立实现复算摘要：`node -e "createHash('sm3')…"` 与 `sm3_digest` 一致（S4-1 已建立，本卡沿用）；
4. 全量单测无回归；`alembic check` 零差异（本卡无迁移）；
5. 文档收口：PROGRESS / DECISIONS（D-075）/ AGENTS §10.8 锚点 / HANDOFF。

## 7. 已知限制（论文"不足"章节素材）

- 签名固定使用国标默认 ID `1234567812345678`：不同用户的签名不按身份区分 ID；
  身份绑定靠 `cert_id` + 记录里的公钥/证书校验（S4-6）。若要按用户 ID 绑定，必须
  签名/验签同期改造，属后续工作；
- 本卡不做密钥生成与私钥保护（S4-5），测试用的固定密钥对**只存在于测试代码**；
- `gmssl` 是纯 Python 实现，SM2 点运算无常数时间保证（侧信道不在本期范围，S6 安全测试如实写）。