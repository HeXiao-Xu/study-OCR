"""SM4-CBC + HMAC-SM3 的 Encrypt-then-MAC 对称层。

用途：S4-4 数字信封用它加密票据影像/字段 JSON；本卡先把"对称层"定死。

```
iv  = secrets.token_bytes(16)
ct  = SM4-CBC(PKCS7(plaintext), iv)
mac = HMAC-SM3(mac_key, aad ‖ iv ‖ ct)      # Encrypt-then-MAC
mac_key = SM3(key ‖ "mac")                  # 加密 key 与 MAC key 必须不同
```

**为什么 CBC 链接与 PKCS7 由本层实现**：gmssl 的 `CryptSM4.crypt_cbc` 在加密时无条件补
PKCS7、解密时无条件去填充，而它的去填充是 `lambda data: data[:-data[-1]]`
（`gmssl/func.py:16`）——**完全不校验填充**，无法满足"去填充失败抛 IntegrityError"。
SM4 分组本体仍由 gmssl 提供（`set_key` + `one_round`），本层只做 XOR 链接与填充校验；
国标 ECB 单块向量（GB/T 32907-2016 A.1）证明分组层正确，CBC 另有 OpenSSL 交叉向量。

**为什么 MAC 覆盖 IV**：只覆盖密文时，攻击者翻转 IV 的第一块就能可控地翻转明文首块
（CBC 的经典构造攻击）。`aad` 留给 S4-4 把信封头（magic/cipher_algo/key_algo/
recipient_cert_fp）一起绑进 MAC。

失败归类（与 AGENTS §10.4 对齐）：`mac_mismatch` / `bad_padding` / `bad_ciphertext`；
结构性长度错误（key 16B、iv 16B、mac 32B）抛 `ValueError`。**解密前必须先验 MAC**，
MAC 不过不产生任何明文。
"""
from __future__ import annotations

import hmac as hmac_module
import secrets
from dataclasses import dataclass
from typing import Final

from gmssl.sm4 import SM4_DECRYPT, SM4_ENCRYPT, CryptSM4

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


def encrypt(key: bytes, plaintext: bytes, *, aad: bytes = b"") -> Sm4Ciphertext:
    """加密并计算 MAC（Encrypt-then-MAC）。"""
    key_bytes = _require_key(key)
    payload = _require_bytes(plaintext, name="plaintext")
    associated = _require_bytes(aad, name="aad")

    iv = secrets.token_bytes(BLOCK_SIZE)
    ciphertext = _cbc_encrypt(key_bytes, iv, _pkcs7_pad(payload))
    mac = hmac_sm3(derive_mac_key(key_bytes), associated + iv + ciphertext)
    return Sm4Ciphertext(iv=iv, ciphertext=ciphertext, mac=mac)


def decrypt(key: bytes, sealed: Sm4Ciphertext, *, aad: bytes = b"") -> bytes:
    """先验 MAC、再解密、最后严格去填充；任何一步失败都抛 `IntegrityError`。"""
    key_bytes = _require_key(key)
    if not isinstance(sealed, Sm4Ciphertext):
        raise TypeError(f"sealed 必须是 Sm4Ciphertext，收到 {type(sealed).__name__}")
    associated = _require_bytes(aad, name="aad")
    if len(sealed.iv) != BLOCK_SIZE:
        raise ValueError("iv 必须是 16 字节")
    if len(sealed.mac) != _MAC_BYTES:
        raise ValueError("MAC 必须是 32 字节")

    expected = hmac_sm3(derive_mac_key(key_bytes), associated + sealed.iv + sealed.ciphertext)
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
    cipher = CryptSM4(mode=SM4_ENCRYPT)
    cipher.set_key(key, SM4_ENCRYPT)
    return bytes(cipher.one_round(cipher.sk, list(block)))


def _cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """CBC 加密；`data` 必须已按 16 字节对齐（填充由 `_pkcs7_pad` 负责）。"""
    if len(iv) != BLOCK_SIZE:
        raise ValueError("iv 必须是 16 字节")
    if len(data) % BLOCK_SIZE:
        raise ValueError("待加密数据必须按 16 字节对齐")
    cipher = CryptSM4(mode=SM4_ENCRYPT)
    cipher.set_key(key, SM4_ENCRYPT)
    previous = iv
    out = bytearray()
    for offset in range(0, len(data), BLOCK_SIZE):
        mixed = _xor(data[offset : offset + BLOCK_SIZE], previous)
        previous = bytes(cipher.one_round(cipher.sk, list(mixed)))
        out += previous
    return bytes(out)


def _cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """CBC 解密，返回**未去填充**的明文（填充校验由 `_pkcs7_unpad` 负责）。"""
    if len(iv) != BLOCK_SIZE:
        raise ValueError("iv 必须是 16 字节")
    if not ciphertext or len(ciphertext) % BLOCK_SIZE:
        raise ValueError("密文必须非空且按 16 字节对齐")
    cipher = CryptSM4(mode=SM4_DECRYPT)
    cipher.set_key(key, SM4_DECRYPT)
    previous = iv
    out = bytearray()
    for offset in range(0, len(ciphertext), BLOCK_SIZE):
        block = ciphertext[offset : offset + BLOCK_SIZE]
        decrypted = bytes(cipher.one_round(cipher.sk, list(block)))
        out += _xor(decrypted, previous)
        previous = block
    return bytes(out)


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


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


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