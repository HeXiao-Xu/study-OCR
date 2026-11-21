"""归档包口令加密（S5-6）：PBKDF2-HMAC-SHA256 + AES-256-GCM 的自描述容器。

为什么不是"标准 ZIP 口令"：Python 的 `zipfile` 只能读加密 ZIP、不能写；WinZip AES 需要 `pyzipper`
（新依赖，AGENTS §2.1 要求先确认）。本期用项目已有的 `cryptography` 做"口令加密容器"：
里面装的是**标准 ZIP 字节**，`scripts/open_archive.py` 用同一实现解开。
格式（全部大端）：

```
magic(16B "STUDYOCR-ARCHIVE") ‖ version(2B "v1") ‖ iterations(4B)
‖ salt(16B) ‖ nonce(12B) ‖ AES-256-GCM(ciphertext ‖ tag)
```

AAD 绑定 `magic ‖ version ‖ iterations`，改头即解密失败；口令错误与包损坏对外统一文案
（不区分，避免给攻击者反馈）。KDF 参数与密钥库一致（200k 次，D-079）。
"""
from __future__ import annotations

import secrets
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

__all__ = [
    "ArchiveOpenError",
    "MAGIC",
    "MIN_ITERATIONS",
    "VERSION",
    "open_archive",
    "seal_archive",
]

MAGIC = b"STUDYOCR-ARCHIVE"
VERSION = b"v1"
DEFAULT_ITERATIONS = 200_000
MIN_ITERATIONS = 200_000
_SALT_LEN = 16
_NONCE_LEN = 12
_HEADER_LEN = len(MAGIC) + len(VERSION) + 4 + _SALT_LEN + _NONCE_LEN

_UNIFORM_ERROR = "口令错误或归档包损坏"


class ArchiveOpenError(Exception):
    """归档包打不开（口令错、被篡改、格式坏）；消息统一，不泄露失败细节。"""

    def __init__(self, message: str = _UNIFORM_ERROR) -> None:
        super().__init__(message)


def seal_archive(
    plaintext: bytes, passphrase: str, *, iterations: int = DEFAULT_ITERATIONS
) -> bytes:
    """把 ZIP 字节加密成归档包；口令 8–128 字符。"""
    _check_passphrase(passphrase)
    if iterations < MIN_ITERATIONS:
        raise ValueError(f"iterations 不得低于 {MIN_ITERATIONS}")
    salt = secrets.token_bytes(_SALT_LEN)
    nonce = secrets.token_bytes(_NONCE_LEN)
    header = (
        MAGIC + VERSION + struct.pack(">I", iterations) + salt + nonce
    )
    key = _derive_key(passphrase, salt, iterations)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _aad(iterations))
    return header + ciphertext


def open_archive(blob: bytes, passphrase: str) -> bytes:
    """解开口令加密的归档包；任何失败都抛 `ArchiveOpenError`（同一文案）。"""
    _check_passphrase(passphrase)
    if len(blob) <= _HEADER_LEN:
        raise ArchiveOpenError()
    if blob[: len(MAGIC)] != MAGIC or blob[len(MAGIC) : len(MAGIC) + len(VERSION)] != VERSION:
        raise ArchiveOpenError()
    offset = len(MAGIC) + len(VERSION)
    (iterations,) = struct.unpack(">I", blob[offset : offset + 4])
    if iterations < MIN_ITERATIONS:
        raise ArchiveOpenError()
    offset += 4
    salt = blob[offset : offset + _SALT_LEN]
    offset += _SALT_LEN
    nonce = blob[offset : offset + _NONCE_LEN]
    offset += _NONCE_LEN
    ciphertext = blob[offset:]

    key = _derive_key(passphrase, salt, iterations)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _aad(iterations))
    except (InvalidTag, ValueError) as exc:
        raise ArchiveOpenError() from exc


def _aad(iterations: int) -> bytes:
    return MAGIC + VERSION + struct.pack(">I", iterations)


def _derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _check_passphrase(passphrase: str) -> None:
    if not 8 <= len(passphrase) <= 128:
        raise ValueError("归档包口令长度必须为 8–128 字符")