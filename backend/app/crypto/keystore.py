"""SM2 私钥的口令保护：PBKDF2-HMAC-SHA256 + AES-256-GCM。

**职责边界**（S4-5）：
- 密钥**由 OpenSSL 3.x CLI 生成**（`scripts/openssl.sh` → `openssl genpkey -algorithm SM2`，D-078）；
  本模块不生成密钥，也不碰随机源之外的任何"自造算法"；
- 本模块只做三件事：① 从 PKCS#8 DER 解出私钥标量 `d`；② 用口令派生 KEK 把 DER 加密成文件；
  ③ 解锁后返回 `(d, 公钥)` 给 S4-2 签名/验签用；
- **私钥明文只在内存**：落盘的是 AES-256-GCM 密文，临时 PEM 由调用方用完即删（`scripts/keygen.py`）。

安全要点：
- PBKDF2 iterations **下限 200000**（写进文件，也在 AAD 里——调低迭代或换算法都会解密失败）；
- AES-GCM 的 AAD 绑定 `{algo, curve, kdf, iterations, key_fp}`，防降级；
- 解锁失败**统一**抛 `KeystoreError("口令错误")`，不区分"错口令/密文被改/元数据被改"（防 oracle）；
- 私钥、口令、KEK 一律不进日志/异常信息/接口响应；
- 不写数据库：SCHEMA `key_records` 只登记 `storage_path`（本模块只产出文件与公开元数据，S5 落库）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
from dataclasses import dataclass
from typing import Final, Literal

from Cryptodome.Util.asn1 import DerOctetString, DerSequence
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pydantic import BaseModel, ConfigDict, Field

from app.crypto.canonical import canonical_json
from app.crypto.sm.sm2 import SM2_N, public_key_from_private

__all__ = [
    "DEFAULT_ITERATIONS",
    "MIN_ITERATIONS",
    "MIN_PASSPHRASE_CHARS",
    "EncryptedPrivateKey",
    "KeyPair",
    "KeystoreError",
    "derive_kek",
    "key_fingerprint",
    "load_keypair",
    "private_key_hex_from_pkcs8",
    "unwrap_private_key",
    "wrap_private_key",
]

DEFAULT_ITERATIONS: Final = 200_000
# 任务卡下限；也是"PBKDF2 太弱等于没保护"这条安全论证的底线。
MIN_ITERATIONS: Final = 200_000
MIN_PASSPHRASE_CHARS: Final = 8
_SALT_BYTES: Final = 16
_NONCE_BYTES: Final = 12
_KEK_BYTES: Final = 32
_D_SCALAR_BYTES: Final = 32
_EC_PUBLIC_KEY_OID: Final = "1.2.840.10045.2.1"
_SM2_CURVE_OID: Final = "1.2.156.10197.1.301"

_ALGO: Final = "SM2"
_CURVE: Final = "sm2p256v1"
_KDF: Final = "PBKDF2-HMAC-SHA256"


class KeystoreError(Exception):
    """解锁失败。对外文案统一为"口令错误"，不区分具体原因。"""


@dataclass(frozen=True)
class KeyPair:
    """解锁后的密钥对（只在内存；调用方用完即弃）。"""

    private_key_hex: str
    public_key_hex: str
    key_fp: str


class EncryptedPrivateKey(BaseModel):
    """口令加密的私钥文件（`.p8.enc` 的 JSON 结构；不含任何明文材料）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    algo: Literal["SM2"] = "SM2"
    curve: Literal["sm2p256v1"] = "sm2p256v1"
    kdf: Literal["PBKDF2-HMAC-SHA256"] = "PBKDF2-HMAC-SHA256"
    iterations: int = Field(ge=MIN_ITERATIONS)
    salt_b64: str = Field(min_length=1)
    nonce_b64: str = Field(min_length=1)
    ciphertext_b64: str = Field(min_length=1)
    key_fp: str = Field(pattern=r"^[0-9a-f]{64}$")


def derive_kek(passphrase: str, salt: bytes, iterations: int = DEFAULT_ITERATIONS) -> bytes:
    """PBKDF2-HMAC-SHA256(口令, 盐, iterations) → 32B KEK。"""
    _validate_passphrase(passphrase)
    _validate_iterations(iterations)
    if not isinstance(salt, (bytes, bytearray, memoryview)):
        raise TypeError("salt 必须是字节串")
    salt_bytes = bytes(salt)
    if len(salt_bytes) != _SALT_BYTES:
        raise ValueError(f"salt 必须是 {_SALT_BYTES} 字节")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEK_BYTES,
        salt=salt_bytes,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def key_fingerprint(public_key_hex: str) -> str:
    """公钥指纹 = `SHA-256(0x04‖X‖Y)` 的 64 位小写 hex（与证书指纹区分）。"""
    normalized = _normalize_public_key_hex(public_key_hex)
    return hashlib.sha256(b"\x04" + bytes.fromhex(normalized)).hexdigest()


def private_key_hex_from_pkcs8(pkcs8_der: bytes) -> str:
    """从 PKCS#8 DER 里解出 32B 私钥标量 `d`（64 位小写 hex）。

    结构：`PrivateKeyInfo = SEQUENCE{ version INTEGER, AlgorithmIdentifier, privateKey OCTET STRING }`，
    其中 OCTET STRING 的内容是 `ECPrivateKey = SEQUENCE{ version INTEGER, privateKey OCTET STRING, … }`。
    只解析这三层，用 pycryptodomex 的 DER 原语，不手写 ASN.1 编解码。
    """
    der = _as_bytes(pkcs8_der, name="pkcs8_der")
    try:
        info = DerSequence()
        info.decode(der)
        if len(info) < 3 or info[0] != 0 or not isinstance(info[2], bytes):
            raise ValueError("PKCS#8 结构不符")
        ec_der = DerOctetString().decode(info[2]).payload
        ec = DerSequence()
        ec.decode(ec_der)
        if len(ec) < 2 or ec[0] != 1 or not isinstance(ec[1], bytes):
            raise ValueError("ECPrivateKey 结构不符")
        scalar = DerOctetString().decode(ec[1]).payload
    except (ValueError, TypeError, IndexError) as exc:
        raise ValueError("不是有效的 PKCS#8 EC 私钥（SM2）") from exc

    if len(scalar) != _D_SCALAR_BYTES:
        raise ValueError("不是有效的 PKCS#8 EC 私钥（SM2）：私钥标量长度不是 32 字节")
    value = int.from_bytes(scalar, "big")
    if not 1 <= value <= SM2_N - 2:
        raise ValueError("不是有效的 PKCS#8 EC 私钥（SM2）：标量超出 [1, n-2]")
    return scalar.hex()


def wrap_private_key(
    pkcs8_der: bytes,
    passphrase: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
) -> EncryptedPrivateKey:
    """把 PKCS#8 DER 加密成可落盘的口令保护文件（PKCS#8 由 OpenSSL 产出）。"""
    der = _as_bytes(pkcs8_der, name="pkcs8_der")
    if not der:
        raise ValueError("pkcs8_der 不能为空")
    _validate_passphrase(passphrase)
    _validate_iterations(iterations)

    private_key_hex = private_key_hex_from_pkcs8(der)
    public_key_hex = public_key_from_private(private_key_hex)
    fingerprint = key_fingerprint(public_key_hex)

    salt = secrets.token_bytes(_SALT_BYTES)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    kek = derive_kek(passphrase, salt, iterations)
    ciphertext = AESGCM(kek).encrypt(nonce, der, _aad(iterations, fingerprint))

    return EncryptedPrivateKey(
        iterations=iterations,
        salt_b64=_b64(salt),
        nonce_b64=_b64(nonce),
        ciphertext_b64=_b64(ciphertext),
        key_fp=fingerprint,
    )


def unwrap_private_key(record: EncryptedPrivateKey, passphrase: str) -> bytes:
    """用口令解开私钥文件，返回 PKCS#8 DER。任何解锁失败都统一抛"口令错误"。"""
    if not isinstance(record, EncryptedPrivateKey):
        raise TypeError(f"record 必须是 EncryptedPrivateKey，收到 {type(record).__name__}")
    _validate_passphrase(passphrase)

    salt = _decode_b64(record.salt_b64)
    nonce = _decode_b64(record.nonce_b64)
    ciphertext = _decode_b64(record.ciphertext_b64)
    if len(salt) != _SALT_BYTES or len(nonce) != _NONCE_BYTES or not ciphertext:
        raise KeystoreError("口令错误")

    try:
        kek = derive_kek(passphrase, salt, record.iterations)
        return AESGCM(kek).decrypt(nonce, ciphertext, _aad(record.iterations, record.key_fp))
    except (InvalidTag, ValueError) as exc:
        # 错口令、密文被改、元数据被改（AAD）在这里表现得完全一样，统一文案防 oracle。
        raise KeystoreError("口令错误") from exc


def load_keypair(record: EncryptedPrivateKey, passphrase: str) -> KeyPair:
    """解锁并返回 `(d, 公钥, 指纹)`，供 S4-2 的签名/验签直接使用。"""
    der = unwrap_private_key(record, passphrase)
    private_key_hex = private_key_hex_from_pkcs8(der)
    public_key_hex = public_key_from_private(private_key_hex)
    fingerprint = key_fingerprint(public_key_hex)
    if fingerprint != record.key_fp:
        # 解密成功但指纹对不上：文件被"换头"或算法字段被改，不能当口令错误混过去。
        raise KeystoreError("密钥文件与指纹不一致")
    return KeyPair(
        private_key_hex=private_key_hex,
        public_key_hex=public_key_hex,
        key_fp=fingerprint,
    )


# ---------------------------------------------------------------- 内部


def _aad(iterations: int, key_fp: str) -> bytes:
    """AAD 绑定算法参数：改 iterations / algo / curve / kdf / 指纹都会让 GCM 认证失败。"""
    return canonical_json(
        {
            "algo": _ALGO,
            "curve": _CURVE,
            "kdf": _KDF,
            "iterations": iterations,
            "key_fp": key_fp,
        }
    )


def _validate_passphrase(passphrase: str) -> None:
    if not isinstance(passphrase, str):
        raise TypeError(f"口令必须是字符串，收到 {type(passphrase).__name__}")
    if len(passphrase) < MIN_PASSPHRASE_CHARS or not passphrase.strip():
        raise ValueError(f"口令至少 {MIN_PASSPHRASE_CHARS} 个字符，且不能是空白")


def _validate_iterations(iterations: int) -> None:
    if not isinstance(iterations, int) or isinstance(iterations, bool):
        raise TypeError("iterations 必须是整数")
    if iterations < MIN_ITERATIONS:
        raise ValueError(f"PBKDF2 迭代次数不得低于 {MIN_ITERATIONS}")


def _normalize_public_key_hex(public_key_hex: str) -> str:
    if not isinstance(public_key_hex, str):
        raise TypeError(f"公钥必须是十六进制字符串，收到 {type(public_key_hex).__name__}")
    normalized = public_key_hex.lower()
    if len(normalized) == 130:
        if not normalized.startswith("04"):
            raise ValueError("130 位公钥必须带未压缩点前缀 04")
        normalized = normalized[2:]
    if len(normalized) != 128 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("公钥必须是 128 位十六进制（或 130 位带 04 前缀）")
    return normalized


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_b64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KeystoreError("口令错误") from exc


def _as_bytes(value: bytes, *, name: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise TypeError(f"{name} 必须是 bytes/bytearray/memoryview，收到 {type(value).__name__}")