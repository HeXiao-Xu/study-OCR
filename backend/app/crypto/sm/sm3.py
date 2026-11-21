"""SM3 摘要与 HMAC-SM3 的唯一入口（双后端：OpenSSL 优先，gmssl 回退）。

为什么必须包一层：

1. `gmssl.sm3.sm3_hash` 收 `list[int]`、返回 hex，且**会原地修改入参**（内部追加填充块，
   实测 `list(b"abc")` 调用后长度从 3 变成 64）——直接给业务用会把调用方的 `bytearray` 改掉；
2. 纯 Python 的 gmssl SM3 很慢（2MB 实测 **7.5s**），而信封要求 2MB 在 1s 内完成——
   因此优先用 stdlib `hashlib` 的 OpenSSL 实现（同样 2MB 实测 **0.008s**），
   平台没有 OpenSSL SM3 时回退 gmssl，保证可移植性。两条路径**同向量测试**（`_OPENSSL_SM3` 可切换）。

`hmac_sm3`（RFC 2104）同理：OpenSSL 可用时走 stdlib `hmac`，否则手写 ipad/opad 回退。
HMAC 不是国标定义内容，没有"国标向量"，测试用 OpenSSL 交叉向量（见 D-076）。
"""
from __future__ import annotations

import hashlib
import hmac as hmac_module
from typing import Final

from gmssl.sm3 import sm3_hash

__all__ = ["hmac_sm3", "sm3_digest", "sm3_hex"]

_DIGEST_BYTES = 32
_DIGEST_HEX_LEN = _DIGEST_BYTES * 2
# SM3 的分组长度（HMAC 按 RFC 2104 用 64 字节块；与 SHA-256 相同）。
_HMAC_BLOCK_BYTES: Final = 64
# 运行时探测：OpenSSL 3 带 SM3 时走 C 实现（快约 1000 倍）。
_OPENSSL_SM3: bool = "sm3" in hashlib.algorithms_available


def sm3_digest(data: bytes | bytearray | memoryview) -> bytes:
    """SM3 摘要，恒定 32 字节。

    只收字节类入参：`str` 一律 `TypeError`，编码是调用方的责任
    （隐式 UTF-8 会掩盖"两边编码不同"的签名 bug）。
    """
    payload = _as_bytes(data, function="sm3_digest")
    if _OPENSSL_SM3:
        return hashlib.new("sm3", payload).digest()
    return _gmssl_sm3_digest(payload)


def sm3_hex(data: bytes | bytearray | memoryview) -> str:
    """64 位小写 hex，**仅用于日志/调试**；签名与存储一律用 `sm3_digest` 的 bytes。"""
    return sm3_digest(data).hex()


def hmac_sm3(
    key: bytes | bytearray | memoryview,
    data: bytes | bytearray | memoryview,
) -> bytes:
    """HMAC-SM3（RFC 2104；SM3 块长 64B、输出 32B），返回 32 字节。

    用途：Encrypt-then-MAC 的完整性校验（S4-3/S4-4）。key 长于 64B 时按 RFC 先哈希；
    调用方负责恒时比较（`hmac.compare_digest`），不要用 `!=` 比 MAC。
    """
    key_bytes = _as_bytes(key, function="hmac_sm3")
    message = _as_bytes(data, function="hmac_sm3")
    if _OPENSSL_SM3:
        return hmac_module.new(key_bytes, message, "sm3").digest()
    return _fallback_hmac_sm3(key_bytes, message)


# ---------------------------------------------------------------- gmssl 回退路径


def _gmssl_sm3_digest(payload: bytes) -> bytes:
    # 必须传副本：sm3_hash 会原地修改入参，传 list(payload) 让副作用只落在临时对象上。
    hex_digest = sm3_hash(list(payload))
    if not isinstance(hex_digest, str) or len(hex_digest) != _DIGEST_HEX_LEN:
        # 库契约被破坏时宁可炸，也不能返回半截摘要让签名域静默失真。
        actual = f"{type(hex_digest).__name__},{len(hex_digest)}" if isinstance(hex_digest, str) else type(hex_digest).__name__
        raise RuntimeError(f"SM3 摘要长度异常：期望 {_DIGEST_HEX_LEN} 位 hex，实际 {actual}")
    try:
        return bytes.fromhex(hex_digest)
    except ValueError as exc:  # gmssl 返回了非十六进制
        raise RuntimeError("SM3 摘要不是合法十六进制") from exc


def _fallback_hmac_sm3(key: bytes, message: bytes) -> bytes:
    if len(key) > _HMAC_BLOCK_BYTES:
        key = _gmssl_sm3_digest(key)
    padded_key = key.ljust(_HMAC_BLOCK_BYTES, b"\x00")
    inner = _gmssl_sm3_digest(bytes(byte ^ 0x36 for byte in padded_key) + message)
    return _gmssl_sm3_digest(bytes(byte ^ 0x5C for byte in padded_key) + inner)


def _as_bytes(
    data: bytes | bytearray | memoryview,
    *,
    function: str = "sm3_digest",
) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    raise TypeError(
        f"{function} 只接受 bytes/bytearray/memoryview，收到 {type(data).__name__}"
    )