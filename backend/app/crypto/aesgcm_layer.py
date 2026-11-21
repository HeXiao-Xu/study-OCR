"""AES-256-GCM 对照实现（S4-9）。

接口与 S4-3 的 SM4 层保持一致：**nonce + 密文 + 认证标签**三段。
- SM4-CBC + HMAC-SM3：IV 16B、MAC 32B（Encrypt-then-MAC，先验 MAC 再解密）；
- AES-256-GCM：nonce **12B**、tag **16B**（AEAD，解密时自动校验）。

安全口径：
- key 必须 32B、nonce 必须 12B、tag 必须 16B，长度不对直接 `ValueError`；
- nonce 由 `secrets` 每次新生成；**本模块不做 nonce 复用检测**（GCM 的固有风险，论文写明）；
- 篡改（密文/标签/nonce/额外认证数据）→ `AesGcmIntegrityError("mac_mismatch")`，不泄露明文。
"""
from __future__ import annotations

import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = ["KEY_BYTES", "NONCE_BYTES", "TAG_BYTES", "AesGcmIntegrityError", "decrypt", "encrypt"]

KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16


class AesGcmIntegrityError(Exception):
    """GCM 认证失败（篡改/错 key/nonce 不符）。`reason` 与国密层的 `IntegrityError.reason` 对齐。"""

    def __init__(self, reason: str = "mac_mismatch") -> None:
        self.reason = reason
        super().__init__(reason)


def encrypt(key: bytes, plaintext: bytes, *, aad: bytes = b"") -> tuple[bytes, bytes, bytes]:
    """加密，返回 `(nonce, ciphertext, tag)`；tag 从 AESGCM 的 `ct‖tag` 尾部切出（16B）。"""
    key_bytes = _require_key(key)
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(key_bytes).encrypt(nonce, _as_bytes(plaintext, "plaintext"), _as_bytes(aad, "aad"))
    return nonce, sealed[:-TAG_BYTES], sealed[-TAG_BYTES:]


def decrypt(
    key: bytes,
    nonce: bytes,
    ciphertext: bytes,
    tag: bytes,
    *,
    aad: bytes = b"",
) -> bytes:
    """解密；任何认证失败抛 `AesGcmIntegrityError`（不返回 None、不泄露明文）。"""
    key_bytes = _require_key(key)
    nonce_bytes = _as_bytes(nonce, "nonce")
    cipher_bytes = _as_bytes(ciphertext, "ciphertext")
    tag_bytes = _as_bytes(tag, "tag")
    if len(nonce_bytes) != NONCE_BYTES:
        raise ValueError(f"GCM nonce 必须是 {NONCE_BYTES} 字节")
    if len(tag_bytes) != TAG_BYTES:
        raise ValueError(f"GCM tag 必须是 {TAG_BYTES} 字节")
    try:
        return AESGCM(key_bytes).decrypt(nonce_bytes, cipher_bytes + tag_bytes, _as_bytes(aad, "aad"))
    except InvalidTag as exc:
        raise AesGcmIntegrityError("mac_mismatch") from exc


def _require_key(key: bytes) -> bytes:
    key_bytes = _as_bytes(key, "key")
    if len(key_bytes) != KEY_BYTES:
        raise ValueError(f"AES-256 key 必须是 {KEY_BYTES} 字节")
    return key_bytes


def _as_bytes(value: bytes, name: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise TypeError(f"{name} 必须是 bytes/bytearray/memoryview，收到 {type(value).__name__}")