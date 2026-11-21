"""SM4-CBC + HMAC-SM3 的 Encrypt-then-MAC 对称层。

用途：S4-4 数字信封用它加密票据影像/字段 JSON。

```
iv  = secrets.token_bytes(16)
ct  = SM4-CBC(PKCS7(plaintext), iv)
mac = HMAC-SM3(mac_key, aad ‖ iv ‖ ct ‖ trailer)   # Encrypt-then-MAC
mac_key = SM3(key ‖ "mac")                          # 加密 key 与 MAC key 必须不同
```

**后端**：SM4 分组与 CBC 由 `cryptography`（OpenSSL C 实现）完成；纯 Python 的 gmssl 版本
2MB 要 14s，无法满足"2MB 影像 1 秒内"（S4-4 验收），性能实测见 D-077。
PKCS7 填充与去填充仍由本层实现并**严格校验**——高层库不替我们判断填充合法性，
而"填充失败必须抛 IntegrityError"是本层的契约。

**为什么 MAC 覆盖 IV 与 trailer**：只覆盖密文时，攻击者翻转 IV 的第一块就能可控地翻转明文首块
（CBC 的经典构造攻击）。`aad` 放信封头（magic/cipher_algo/key_algo/version）与 `wrapped_key`，
`trailer` 放收方证书指纹——顺序与 SCHEMA `envelopes.mac_b64` 的说明一致（见 D-077）。

失败归类（与 AGENTS §10.4 对齐）：`mac_mismatch` / `bad_padding` / `bad_ciphertext`；
结构性长度错误（key 16B、iv 16B、mac 32B）抛 `ValueError`。**解密前必须先验 MAC**，
MAC 不过不产生任何明文。
"""
from __future__ import annotations

import hmac as hmac_module
import secrets
from dataclasses import dataclass
from typing import Final

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.crypto.sm.sm3 import hmac_sm3, sm3_digest

__all__ = [
    "BLOCK_SIZE",
    "IntegrityError",
    "Sm4Ciphertext",
    "decrypt",
    "derive_mac_key",
    "encrypt",
]

BLOCK_SIZE: Final = 16
_KEY_BYTES: Final = 16
_MAC_BYTES: Final = 32
_MAC_DERIVATION_LABEL: Final = b"mac"

if not hasattr(algorithms, "SM4"):  # pragma: no cover - 环境级缺陷，启动即暴露
    raise ImportError("当前 OpenSSL/cryptography 未提供 SM4（本项目的对称层依赖它）")


class IntegrityError(Exception):
    """完整性校验失败（MAC 不符 / 填充非法 / 密文结构非法）。"""

    def __init__(self, reason: str) -> None:
        # 只带归类，不带任何明文/密钥/中间值（错误信息会进日志与响应）。
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Sm4Ciphertext:
    """对称加密的产物；字段都是 bytes，hex/base64 由调用方按需转换（hex 只进日志）。"""

    iv: bytes
    ciphertext: bytes
    mac: bytes


def derive_mac_key(key: bytes) -> bytes:
    """由加密 key 派生独立的 MAC key（`SM3(key ‖ "mac")`），避免同一把 key 两种用途。"""
    return sm3_digest(_require_key(key) + _MAC_DERIVATION_LABEL)


def encrypt(
    key: bytes,
    plaintext: bytes,
    *,
    aad: bytes = b"",
    trailer: bytes = b"",
) -> Sm4Ciphertext:
    """加密并计算 MAC（Encrypt-then-MAC）。

    `aad` / `trailer` 是调用方要绑定的关联数据（信封头与证书指纹），
    MAC 输入固定为 `aad ‖ IV ‖ 密文 ‖ trailer`。
    """
    key_bytes = _require_key(key)
    payload = _require_bytes(plaintext, name="plaintext")
    associated = _require_bytes(aad, name="aad")
    trailing = _require_bytes(trailer, name="trailer")

    iv = secrets.token_bytes(BLOCK_SIZE)
    ciphertext = _cbc_encrypt(key_bytes, iv, _pkcs7_pad(payload))
    mac = hmac_sm3(derive_mac_key(key_bytes), associated + iv + ciphertext + trailing)
    return Sm4Ciphertext(iv=iv, ciphertext=ciphertext, mac=mac)


def decrypt(
    key: bytes,
    sealed: Sm4Ciphertext,
    *,
    aad: bytes = b"",
    trailer: bytes = b"",
) -> bytes:
    """先验 MAC、再解密、最后严格去填充；任何一步失败都抛 `IntegrityError`。"""
    key_bytes = _require_key(key)
    if not isinstance(sealed, Sm4Ciphertext):
        raise TypeError(f"sealed 必须是 Sm4Ciphertext，收到 {type(sealed).__name__}")
    associated = _require_bytes(aad, name="aad")
    trailing = _require_bytes(trailer, name="trailer")
    if len(sealed.iv) != BLOCK_SIZE:
        raise ValueError("iv 必须是 16 字节")
    if len(sealed.mac) != _MAC_BYTES:
        raise ValueError("MAC 必须是 32 字节")

    expected = hmac_sm3(
        derive_mac_key(key_bytes),
        associated + sealed.iv + sealed.ciphertext + trailing,
    )
    # 恒定时间比较：避免通过响应时间侧信道逐字节猜测 MAC。
    if not hmac_module.compare_digest(expected, sealed.mac):
        raise IntegrityError("mac_mismatch")

    if not sealed.ciphertext or len(sealed.ciphertext) % BLOCK_SIZE:
        raise IntegrityError("bad_ciphertext")
    return _pkcs7_unpad(_cbc_decrypt(key_bytes, sealed.iv, sealed.ciphertext))


# ---------------------------------------------------------------- 内部：分组 / CBC / PKCS7


def _encrypt_block(key: bytes, block: bytes) -> bytes:
    """SM4 单分组加密（仅供国标向量测试；业务一律走 `encrypt`）。"""
    if len(block) != BLOCK_SIZE:
        raise ValueError("分组必须是 16 字节")
    encryptor = Cipher(algorithms.SM4(key), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def _cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """CBC 加密；`data` 必须已按 16 字节对齐（填充由 `_pkcs7_pad` 负责）。"""
    if len(iv) != BLOCK_SIZE:
        raise ValueError("iv 必须是 16 字节")
    if len(data) % BLOCK_SIZE:
        raise ValueError("待加密数据必须按 16 字节对齐")
    encryptor = Cipher(algorithms.SM4(key), modes.CBC(iv)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """CBC 解密，返回**未去填充**的明文（填充校验由 `_pkcs7_unpad` 负责）。"""
    if len(iv) != BLOCK_SIZE:
        raise ValueError("iv 必须是 16 字节")
    if not ciphertext or len(ciphertext) % BLOCK_SIZE:
        raise ValueError("密文必须非空且按 16 字节对齐")
    decryptor = Cipher(algorithms.SM4(key), modes.CBC(iv)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def _pkcs7_pad(data: bytes) -> bytes:
    padding = BLOCK_SIZE - len(data) % BLOCK_SIZE
    return data + bytes([padding]) * padding


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % BLOCK_SIZE:
        raise IntegrityError("bad_ciphertext")
    padding = data[-1]
    # 严格校验：1–16 且末尾 padding 个字节必须全等于 padding，否则视为被篡改。
    if not 1 <= padding <= BLOCK_SIZE or data[-padding:] != bytes([padding]) * padding:
        raise IntegrityError("bad_padding")
    return data[:-padding]


def _require_key(key: bytes) -> bytes:
    key_bytes = _require_bytes(key, name="key")
    if len(key_bytes) != _KEY_BYTES:
        raise ValueError(f"SM4 key 必须是 {_KEY_BYTES} 字节")
    return key_bytes


def _require_bytes(value: bytes, *, name: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise TypeError(f"{name} 必须是 bytes/bytearray/memoryview，收到 {type(value).__name__}")