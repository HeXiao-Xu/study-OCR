"""算法可替换层（S4-9）：同接口的国密实现与 RSA/AES 对照实现。

为什么需要它：S6 的性能实验要比较"国密 vs RSA/AES"，**必须是同一个接口换实现**；
接口一换、参数一漂，数字就不可比（任务卡坑）。

- 默认实现 `SMProvider`：SM2（签名/包裹 CEK）+ SM4-CBC + HMAC-SM3（Encrypt-then-MAC），
  直接复用 S4-2/S4-3 的封装，**不重写算法**；
- 对照实现 `RSAProvider`：RSA-PSS-SHA256 签名 + RSA-OAEP-SHA256 包裹 CEK + AES-256-GCM；
- S5 按配置选择，**默认国密**；本卡不改 `envelope.py`，也不改国密层参数。

密钥表示：`KeyMaterial` 里 `public_key/private_key` 对调用方不透明——
SM 是十六进制 ASCII 字节，RSA 是 PEM 字节；这样两套接口才能同名同参。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.crypto import aesgcm_layer, rsa_layer
from app.crypto.sm import sm2
from app.crypto.sm.sm4 import (
    IntegrityError as Sm4IntegrityError,
)
from app.crypto.sm.sm4 import (
    Sm4Ciphertext,
    decrypt as sm4_decrypt,
    encrypt as sm4_encrypt,
)

__all__ = [
    "AlgoIntegrityError",
    "AlgoProvider",
    "KeyMaterial",
    "RSAProvider",
    "SMProvider",
    "SymmetricPayload",
    "get_provider",
]


class AlgoIntegrityError(Exception):
    """两套实现统一的完整性失败（`reason` 与国密层 `IntegrityError.reason` 对齐）。"""

    def __init__(self, reason: str = "integrity_failure") -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class KeyMaterial:
    """不透明密钥材料：SM 为 hex ASCII 字节，RSA 为 PEM 字节。"""

    algo: str
    public_key: bytes
    private_key: bytes
    key_fp: str


@dataclass(frozen=True)
class SymmetricPayload:
    """对称加密产物（两套实现同一形态）。"""

    nonce: bytes  # SM4: IV 16B；AES-GCM: nonce 12B
    ciphertext: bytes
    tag: bytes  # SM4: HMAC-SM3 32B；AES-GCM: tag 16B


@runtime_checkable
class AlgoProvider(Protocol):
    """S5/S6 依赖的唯一算法接口；同名同参是公平对比的前提。"""

    name: str
    symmetric_key_bytes: int

    def keygen(self) -> KeyMaterial: ...
    def sign(self, payload: bytes, key: KeyMaterial) -> bytes: ...
    def verify(self, payload: bytes, signature: bytes, key: KeyMaterial) -> bool: ...
    def encrypt_key(self, cek: bytes, key: KeyMaterial) -> bytes: ...
    def decrypt_key(self, wrapped: bytes, key: KeyMaterial) -> bytes: ...
    def wrap_symmetric(self, cek: bytes, plaintext: bytes, *, aad: bytes = b"") -> SymmetricPayload: ...
    def unwrap_symmetric(self, cek: bytes, payload: SymmetricPayload, *, aad: bytes = b"") -> bytes: ...


class SMProvider:
    """国密实现（默认）：SM2 + SM4-CBC + HMAC-SM3。"""

    name = "sm"
    symmetric_key_bytes = 16  # SM4 key

    def keygen(self) -> KeyMaterial:
        raise NotImplementedError(
            "SM2 密钥由 scripts/keygen.py 生成（OpenSSL，D-078）；provider 不重复实现密钥生成"
        )

    @staticmethod
    def key_from_hex(private_key_hex: str, public_key_hex: str) -> KeyMaterial:
        """用已有 SM2 密钥构造 `KeyMaterial`（S5 从 keystore 解锁后走这里）。"""
        public = _normalize_sm_public(public_key_hex)
        private = private_key_hex.strip().lower()
        if len(private) != 64 or any(ch not in "0123456789abcdef" for ch in private):
            raise ValueError("SM2 私钥必须是 64 位十六进制")
        return KeyMaterial(
            algo="SM2",
            public_key=public.encode("ascii"),
            private_key=private.encode("ascii"),
            key_fp=hashlib.sha256(b"\x04" + bytes.fromhex(public)).hexdigest(),
        )

    def sign(self, payload: bytes, key: KeyMaterial) -> bytes:
        return bytes.fromhex(
            sm2.sign_bytes(
                payload,
                private_key_hex=key.private_key.decode("ascii"),
                public_key_hex=key.public_key.decode("ascii"),
            )
        )

    def verify(self, payload: bytes, signature: bytes, key: KeyMaterial) -> bool:
        return sm2.verify_bytes(
            payload,
            bytes(signature).hex(),
            public_key_hex=key.public_key.decode("ascii"),
        )

    def encrypt_key(self, cek: bytes, key: KeyMaterial) -> bytes:
        return sm2.encrypt_bytes(cek, public_key_hex=key.public_key.decode("ascii"))

    def decrypt_key(self, wrapped: bytes, key: KeyMaterial) -> bytes:
        return sm2.decrypt_bytes(wrapped, private_key_hex=key.private_key.decode("ascii"))

    def wrap_symmetric(self, cek: bytes, plaintext: bytes, *, aad: bytes = b"") -> SymmetricPayload:
        sealed = sm4_encrypt(cek, plaintext, aad=aad)
        return SymmetricPayload(nonce=sealed.iv, ciphertext=sealed.ciphertext, tag=sealed.mac)

    def unwrap_symmetric(self, cek: bytes, payload: SymmetricPayload, *, aad: bytes = b"") -> bytes:
        try:
            return sm4_decrypt(
                cek,
                Sm4Ciphertext(iv=payload.nonce, ciphertext=payload.ciphertext, mac=payload.tag),
                aad=aad,
            )
        except Sm4IntegrityError as exc:
            raise AlgoIntegrityError(exc.reason) from exc


class RSAProvider:
    """对照实现：RSA-PSS-SHA256 签名 + RSA-OAEP-SHA256 包裹 + AES-256-GCM。"""

    name = "rsa"
    symmetric_key_bytes = aesgcm_layer.KEY_BYTES  # 32

    def __init__(self, key_size: int = 2048) -> None:
        if key_size not in rsa_layer.ALLOWED_KEY_SIZES:
            raise ValueError(f"RSA 密钥位数只允许 {sorted(rsa_layer.ALLOWED_KEY_SIZES)}")
        self.key_size = key_size

    def keygen(self) -> KeyMaterial:
        private_pem, public_pem = rsa_layer.generate_keypair(self.key_size)
        return KeyMaterial(
            algo="RSA",
            public_key=public_pem,
            private_key=private_pem,
            key_fp=hashlib.sha256(public_pem).hexdigest(),
        )

    def sign(self, payload: bytes, key: KeyMaterial) -> bytes:
        return rsa_layer.sign(payload, key.private_key)

    def verify(self, payload: bytes, signature: bytes, key: KeyMaterial) -> bool:
        return rsa_layer.verify(payload, signature, key.public_key)

    def encrypt_key(self, cek: bytes, key: KeyMaterial) -> bytes:
        return rsa_layer.encrypt_key(cek, key.public_key)

    def decrypt_key(self, wrapped: bytes, key: KeyMaterial) -> bytes:
        return rsa_layer.decrypt_key(wrapped, key.private_key)

    def wrap_symmetric(self, cek: bytes, plaintext: bytes, *, aad: bytes = b"") -> SymmetricPayload:
        nonce, ciphertext, tag = aesgcm_layer.encrypt(cek, plaintext, aad=aad)
        return SymmetricPayload(nonce=nonce, ciphertext=ciphertext, tag=tag)

    def unwrap_symmetric(self, cek: bytes, payload: SymmetricPayload, *, aad: bytes = b"") -> bytes:
        try:
            return aesgcm_layer.decrypt(
                cek,
                payload.nonce,
                payload.ciphertext,
                payload.tag,
                aad=aad,
            )
        except aesgcm_layer.AesGcmIntegrityError as exc:
            raise AlgoIntegrityError(exc.reason) from exc


def get_provider(name: str = "sm") -> AlgoProvider:
    """按名字取实现；**默认国密**；未知名字直接报错（不静默回退）。"""
    key = (name or "sm").strip().lower()
    if key in {"sm", "gm", "国密", "sm2"}:
        return SMProvider()
    if key in {"rsa", "rsa-oaep", "aes", "aes-gcm"}:
        return RSAProvider()
    raise ValueError(f"未知算法实现：{name!r}（可选 sm / rsa）")


def _normalize_sm_public(public_key_hex: str) -> str:
    normalized = public_key_hex.strip().lower()
    if len(normalized) == 130:
        if not normalized.startswith("04"):
            raise ValueError("130 位公钥必须带未压缩点前缀 04")
        normalized = normalized[2:]
    if len(normalized) != 128 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError("SM2 公钥必须是 128 位十六进制（或 130 位带 04 前缀）")
    return normalized