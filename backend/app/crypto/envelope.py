"""数字信封：SM2 包裹会话密钥 + SM4-CBC Encrypt-then-MAC。

```
CEK         = secrets.token_bytes(16)                      # 每次信封重新生成
wrapped_key = SM2_C1C3C2(收方公钥, CEK)                     # 64B C1 ‖ 32B C3 ‖ 16B C2
header      = magic ‖ cipher_mode ‖ key_algo ‖ payload_version ‖ wrapped_key
ct, mac     = SM4-CBC-EtM(CEK, plaintext, aad=header, trailer=recipient_cert_fp)
```

MAC 覆盖 `header ‖ IV ‖ 密文 ‖ 收方证书指纹`，其中 `header` 已含 `wrapped_key`——这样：

- 吻合 SCHEMA `envelopes.mac_b64` 说明的覆盖顺序（`header‖IV‖密文‖收方证书指纹`）；
- **多绑了 `wrapped_key`**：本卡按 AGENTS §10.4 不依赖 SM2 的 C3 做完整性判断，
  若不把包裹密钥绑进 MAC，攻击者单独改 C3 段（改不到 CEK）就无人发现（D-077）。

拆封顺序（任务卡口径）：**解 CEK → 验 MAC 解密 → `payload_sha256` 比对**。
AGENTS §10.4 写的"验 MAC → 解 CEK"在物理上不可能——MAC key 由 CEK 派生（`SM3(CEK‖"mac")`），
必须先解出 CEK 才能验 MAC；这一点在 D-077 里说明。

失败一律归入 `EnvelopeError.reason`：`bad_envelope`（结构/base64/长度）/
`mac_mismatch` / `bad_padding` / `digest_mismatch`。证书链与签名校验不在这里（S4-6 / S5）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.crypto.sm.sm2 import decrypt_bytes, encrypt_bytes
from app.crypto.sm.sm4 import IntegrityError, Sm4Ciphertext, decrypt, encrypt

__all__ = ["Envelope", "EnvelopeError", "open_sealed", "seal"]

_MAGIC: Final = b"study-ocr-envelope"
_SEPARATOR: Final = b"\x00"
_CEK_BYTES: Final = 16


class EnvelopeError(Exception):
    """信封拆封失败；`reason` 用于上层归类（不泄露明文或密钥）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Envelope(BaseModel):
    """一次数字信封（S5 直接映射到 `envelopes` 行；大影像的对象名属业务层）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cipher_mode: Literal["SM4-CBC"] = "SM4-CBC"
    key_algo: Literal["SM2"] = "SM2"
    iv_b64: str = Field(min_length=1)
    ciphertext_b64: str = Field(min_length=1)
    mac_b64: str = Field(min_length=1)
    wrapped_key_b64: str = Field(min_length=1)
    recipient_cert_fp: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_version: str = Field(default="v1", min_length=1, max_length=32)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at 必须带时区（统一用 UTC）")
        return value


def seal(
    plaintext: bytes,
    *,
    recipient_public_key_hex: str,
    recipient_cert_fp: str,
    payload_version: str = "v1",
    now: datetime | None = None,
) -> Envelope:
    """给收方公钥封装明文载荷（票据影像/字段 JSON 的字节）。"""
    payload = _as_bytes(plaintext, name="plaintext")
    cert_fp = _normalize_cert_fp(recipient_cert_fp)

    cek = secrets.token_bytes(_CEK_BYTES)
    wrapped_key = encrypt_bytes(cek, public_key_hex=recipient_public_key_hex)
    associated = _mac_aad("SM4-CBC", "SM2", payload_version, wrapped_key)
    sealed = encrypt(cek, payload, aad=associated, trailer=cert_fp.encode("ascii"))

    return Envelope(
        iv_b64=_b64(sealed.iv),
        ciphertext_b64=_b64(sealed.ciphertext),
        mac_b64=_b64(sealed.mac),
        wrapped_key_b64=_b64(wrapped_key),
        recipient_cert_fp=cert_fp,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_version=payload_version,
        created_at=now or datetime.now(timezone.utc),
    )


def open_sealed(envelope: Envelope, *, private_key_hex: str) -> bytes:
    """解开信封：解 CEK → 验 MAC 解密 → 校验 `payload_sha256`。"""
    if not isinstance(envelope, Envelope):
        raise TypeError(f"envelope 必须是 Envelope，收到 {type(envelope).__name__}")

    iv = _decode_b64(envelope.iv_b64)
    ciphertext = _decode_b64(envelope.ciphertext_b64)
    mac = _decode_b64(envelope.mac_b64)
    wrapped_key = _decode_b64(envelope.wrapped_key_b64)
    if iv is None or ciphertext is None or mac is None or wrapped_key is None:
        raise EnvelopeError("bad_envelope")

    try:
        cek = decrypt_bytes(wrapped_key, private_key_hex=private_key_hex)
    except ValueError as exc:  # 包裹密钥结构非法 / C1 不在曲线上
        raise EnvelopeError("bad_envelope") from exc

    associated = _mac_aad(
        envelope.cipher_mode, envelope.key_algo, envelope.payload_version, wrapped_key
    )
    try:
        payload = decrypt(
            cek,
            Sm4Ciphertext(iv=iv, ciphertext=ciphertext, mac=mac),
            aad=associated,
            trailer=envelope.recipient_cert_fp.encode("ascii"),
        )
    except IntegrityError as exc:  # mac_mismatch / bad_padding / bad_ciphertext
        raise EnvelopeError(exc.reason) from exc
    except ValueError as exc:  # iv/mac 长度非法
        raise EnvelopeError("bad_envelope") from exc

    if hashlib.sha256(payload).hexdigest() != envelope.payload_sha256:
        raise EnvelopeError("digest_mismatch")
    return payload


# ---------------------------------------------------------------- 内部


def _mac_aad(cipher_mode: str, key_algo: str, payload_version: str, wrapped_key: bytes) -> bytes:
    """MAC 的关联数据：magic ‖ cipher_mode ‖ key_algo ‖ payload_version ‖ wrapped_key。

    顺序与编码写死；任何字段被改都会让 MAC 失配（认证的是信封元数据本身）。
    """
    return _SEPARATOR.join(
        [
            _MAGIC,
            cipher_mode.encode("utf-8"),
            key_algo.encode("utf-8"),
            payload_version.encode("utf-8"),
            wrapped_key,
        ]
    )


def _normalize_cert_fp(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"recipient_cert_fp 必须是字符串，收到 {type(value).__name__}")
    normalized = value.lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("recipient_cert_fp 必须是 64 位十六进制（SHA-256 指纹）")
    return normalized


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_b64(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _as_bytes(value: bytes, *, name: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise TypeError(f"{name} 必须是 bytes/bytearray/memoryview，收到 {type(value).__name__}")