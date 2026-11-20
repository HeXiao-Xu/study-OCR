"""SM3 摘要的唯一入口。

为什么必须包一层：`gmssl.sm3.sm3_hash` ① 收 `list[int]` ② 返回 hex 字符串
③ **会原地修改入参**（内部追加填充块，实测 `list(b"abc")` 调用后长度从 3 变成 64）。
直接暴露给业务会带来两个真实事故：调用方的 `bytearray` 被改掉、每个调用点各写一遍
hex↔bytes 转换导致出现半截摘要。因此 AGENTS §6 规定**业务代码禁止直接 import gmssl**，
只允许经本模块。

本卡只实现摘要（无随机数）；`hmac_sm3` 属 S4-3 的 Encrypt-then-MAC，届时在此模块补齐。
"""
from __future__ import annotations

from gmssl.sm3 import sm3_hash

__all__ = ["sm3_digest", "sm3_hex"]

_DIGEST_BYTES = 32
_DIGEST_HEX_LEN = _DIGEST_BYTES * 2


def sm3_digest(data: bytes | bytearray | memoryview) -> bytes:
    """SM3 摘要，恒定 32 字节。

    只收字节类入参：`str` 一律 `TypeError`，编码是调用方的责任
    （隐式 UTF-8 会掩盖"两边编码不同"的签名 bug）。
    """
    payload = _as_bytes(data)
    # 必须传副本：sm3_hash 会原地修改入参，传 list(payload) 让副作用只落在临时对象上。
    hex_digest = sm3_hash(list(payload))
    if not isinstance(hex_digest, str) or len(hex_digest) != _DIGEST_HEX_LEN:
        # 库契约被破坏时宁可炸，也不能返回半截摘要让签名域静默失真。
        actual = f"{type(hex_digest).__name__},{len(hex_digest)}" if isinstance(hex_digest, str) else type(hex_digest).__name__
        raise RuntimeError(
            f"SM3 摘要长度异常：期望 {_DIGEST_HEX_LEN} 位 hex，实际 {actual}"
        )
    try:
        return bytes.fromhex(hex_digest)
    except ValueError as exc:  # gmssl 返回了非十六进制
        raise RuntimeError("SM3 摘要不是合法十六进制") from exc


def sm3_hex(data: bytes | bytearray | memoryview) -> str:
    """64 位小写 hex，**仅用于日志/调试**；签名与存储一律用 `sm3_digest` 的 bytes。"""
    return sm3_digest(data).hex()


def _as_bytes(data: bytes | bytearray | memoryview) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    raise TypeError(
        f"sm3_digest 只接受 bytes/bytearray/memoryview，收到 {type(data).__name__}"
    )