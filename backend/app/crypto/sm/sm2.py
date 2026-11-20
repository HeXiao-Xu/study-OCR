"""SM2 签名/验签的唯一入口（SM2-SM3，含国标 ZA 绑定）。

**签名策略（写死，S5 不得绕过）**：只走 gmssl 的 `sign_with_sm3` / `verify_with_sm3`，
即按 GB/T 32918.2 计算 `e = SM3(ZA ‖ M)` 后再签名。

- 绝不调用 `sign(data, K)` 直接签外部摘要：那会丢掉 `ZA`（用户 ID‖曲线参数‖公钥）绑定，
  不是国标 SM2 签名，而且和 `sign_with_sm3` 混用会造成**双重哈希**——签名方与验签方
  各算一次 SM3，恒验不过且极难排查；
- 封装层**不做任何哈希**：SM3 只由 gmssl 内部按国标调用一次。

必须处理的 gmssl==3.2.2 行为（每条都有对应测试）：

1. `mode` 默认 0（C1C2C3），文档字符串却写 "default is 1" → 显式 `mode=1`；
2. `public_key.lstrip("04")` 是**按字符集剥离**而不是去前缀：公钥 x 坐标以 `04` 开头时
   会被剥坏（约 1/256 的密钥）→ 入口归一化成 128 hex，构造后**强制覆盖** `sm2.public_key`；
3. `sign()` 遇到 `r=0`、`r+k=n`、`s=0` 返回 `None` 且不重试 → 封装层换 K 重试（有上限）；
4. `sign_with_sm3(data, None)` 用 `func.random_hex`（基于 `random`，K 可预测）→
   **永远显式传 K**，只来自 `secrets` + 拒绝采样；
5. `verify()` 不校验 `r, s ∈ [1, n-1]`（`s+n` 的变体也能验过）→ 验签前做范围校验；
6. `verify()` 对畸形签名抛异常、`t=0` 时返回 int `0` → 封装层统一 `bool`，畸形一律 `False`；
7. `_sm3_z` 固定使用国标默认 ID `1234567812345678` → 导出为 `DEFAULT_USER_ID`，
   将来换 ID 必须签名/验签同期改造并记决策。

签名值统一用 **DER `SEQUENCE{ r INTEGER, s INTEGER }` 的十六进制**（约 70–72 字节），
与 SCHEMA `signatures.signature_b64`（base64）一一对应；不用裸 `r‖s`。
"""
from __future__ import annotations

import binascii
import secrets
from typing import Final

from Cryptodome.Util.asn1 import DerSequence
from gmssl.sm2 import CryptSM2

__all__ = ["DEFAULT_USER_ID", "SM2_N", "sign_bytes", "verify_bytes"]

# 国标推荐的 256 位素域曲线 sm2p256v1（GB/T 32918.5）
SM2_P: Final = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF
SM2_A: Final = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC
SM2_B: Final = 0x28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93
SM2_N: Final = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123

# 国标默认用户标识（ENTLA = 0x0080）；ZA 由它 + 曲线参数 + 公钥算出。
DEFAULT_USER_ID: Final = b"1234567812345678"

_MAX_SIGN_ATTEMPTS: Final = 8
_HEX_DIGITS: Final = frozenset("0123456789abcdef")


def sign_bytes(
    data: bytes | bytearray | memoryview,
    *,
    private_key_hex: str,
    public_key_hex: str,
    k_hex: str | None = None,
) -> str:
    """对 `data` 做 SM2-SM3 签名，返回 DER 签名值的十六进制。

    `data` 是**待签字节**（本项目 = `canonical_json(...)` 的输出），不是摘要。
    `k_hex` 只允许国标向量/确定性测试使用：**生产代码永远不传**，
    复用 K 会直接泄漏私钥（SM2 与 ECDSA 同理）。
    """
    payload = _as_bytes(data)
    private_key = _validate_private_key(private_key_hex)
    public_key = _normalize_public_key(public_key_hex)
    fixed_k = _validate_scalar(k_hex, name="k") if k_hex is not None else None

    sm2 = _build(private_key, public_key)
    for _ in range(_MAX_SIGN_ATTEMPTS):
        k = fixed_k if fixed_k is not None else _random_scalar_hex()
        signature = sm2.sign_with_sm3(payload, k)
        if signature:
            return signature
        if fixed_k is not None:
            # 固定 K 时不能静默换 K，否则"确定性"用例会变成假绿。
            raise RuntimeError("固定 K 产生的签名无效（r=0 / r+k=n / s=0），请换一个 K")
    raise RuntimeError("SM2 签名连续失败：K 反复无效，疑似随机源异常")


def verify_bytes(
    data: bytes | bytearray | memoryview,
    signature_hex: str,
    *,
    public_key_hex: str,
) -> bool:
    """验签。**畸形签名返回 False**（验签不是"抛异常"的语义）；公钥结构性错误抛 `ValueError`。"""
    payload = _as_bytes(data)
    public_key = _normalize_public_key(public_key_hex)
    scalars = _decode_signature(signature_hex)
    if scalars is None:
        return False
    r, s = scalars
    # 国标 B1/B2：r、s 必须落在 [1, n-1]；gmssl 不查，s+n 的变体能验过（可塑性）。
    if not (1 <= r < SM2_N and 1 <= s < SM2_N):
        return False

    sm2 = _build("", public_key)
    try:
        return bool(sm2.verify_with_sm3(signature_hex, payload))
    except (ValueError, TypeError, binascii.Error, AttributeError):
        # DER 虽可解析、但内部点运算仍可能因越界/畸形输入抛错；一律视为验签失败。
        return False


# ---------------------------------------------------------------- 内部实现


def _build(private_key_hex: str, public_key_hex: str) -> CryptSM2:
    sm2 = CryptSM2(private_key=private_key_hex, public_key=public_key_hex, mode=1, asn1=True)
    # gmssl 的 __init__ 会执行 public_key.lstrip("04")（按字符集剥离，会吃掉 x 开头的 04）；
    # 这里覆盖回归一化后的值，确保 ZA 与验签都用正确的公钥。
    sm2.public_key = public_key_hex
    return sm2


def _as_bytes(data: bytes | bytearray | memoryview) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    raise TypeError(f"SM2 只接受 bytes/bytearray/memoryview，收到 {type(data).__name__}")


def _validate_private_key(value: str) -> str:
    normalized = _normalize_hex(value, name="私钥", digits=64)
    scalar = int(normalized, 16)
    # GB/T 32918.1：d ∈ [1, n-2]
    if not (1 <= scalar <= SM2_N - 2):
        raise ValueError("私钥超出合法范围 [1, n-2]")
    return normalized


def _normalize_public_key(value: str) -> str:
    candidate = _normalize_hex(value, name="公钥", digits=(128, 130))
    if len(candidate) == 130:
        if not candidate.startswith("04"):
            raise ValueError("公钥 130 位十六进制必须带未压缩点前缀 04")
        candidate = candidate[2:]
    x = int(candidate[:64], 16)
    y = int(candidate[64:], 16)
    if not (x < SM2_P and y < SM2_P):
        raise ValueError("公钥坐标超出素域范围")
    if pow(y, 2, SM2_P) != (pow(x, 3, SM2_P) + SM2_A * x + SM2_B) % SM2_P:
        raise ValueError("公钥不在 sm2p256v1 曲线上")
    return candidate


def _normalize_hex(value: str, *, name: str, digits: int | tuple[int, ...]) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name}必须是十六进制字符串，收到 {type(value).__name__}")
    allowed = (digits,) if isinstance(digits, int) else digits
    normalized = value.lower()
    if len(normalized) not in allowed or not set(normalized) <= _HEX_DIGITS:
        raise ValueError(f"{name}格式非法：期望 {'/'.join(map(str, allowed))} 位十六进制")
    return normalized


def _validate_scalar(value: str, *, name: str) -> str:
    normalized = _normalize_hex(value, name=name, digits=64)
    scalar = int(normalized, 16)
    if not (1 <= scalar < SM2_N):
        raise ValueError(f"{name} 超出合法范围 [1, n-1]")
    return normalized


def _random_scalar_hex() -> str:
    """K ∈ [1, n-1]：只允许 `secrets`（AGENTS §6），拒绝采样避免越界。"""
    while True:
        candidate = secrets.token_hex(32)
        if 1 <= int(candidate, 16) < SM2_N:
            return candidate


def _decode_signature(signature_hex: str) -> tuple[int, int] | None:
    if not isinstance(signature_hex, str):
        raise TypeError(f"签名必须是十六进制字符串，收到 {type(signature_hex).__name__}")
    if not signature_hex or len(signature_hex) % 2:
        return None
    try:
        raw = bytes.fromhex(signature_hex)
    except ValueError:
        return None
    sequence = DerSequence()
    try:
        sequence.decode(raw)
    except (ValueError, TypeError, IndexError):
        return None
    if len(sequence) != 2:
        return None
    r, s = sequence[0], sequence[1]
    if not (isinstance(r, int) and isinstance(s, int)):
        return None
    return r, s