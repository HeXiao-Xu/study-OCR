"""RSA-OAEP-SHA256 / RSA-PSS-SHA256 对照实现（S4-9，仅供性能/算法对比，不进生产默认链路）。

与国密层的对应关系：

| 国密（默认） | 本文件 |
|---|---|
| SM2 包裹 16B CEK（C1C3C2） | RSA-OAEP-SHA256 包裹 CEK |
| SM2-SM3 对 canonical 字节签名 | RSA-PSS（MGF1-SHA256）对同一字节签名 |

**显式指定 hash**：OAEP 与 PSS 的 hash 和 MGF1 hash 全部写死 SHA-256。
不同库/版本默认值不同，靠默认值会让"同一接口"在 S6 对比时口径漂移（任务卡坑）。

密钥格式：PKCS#8 私钥 PEM + SubjectPublicKeyInfo 公钥 PEM（本卡用于测试/基准，**未做口令保护**；
生产私钥保护仍走 S4-5，本卡未接入）。
"""
from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

__all__ = ["ALLOWED_KEY_SIZES", "decrypt_key", "encrypt_key", "generate_keypair", "sign", "verify"]

ALLOWED_KEY_SIZES = frozenset({2048, 3072})

# 显式 SHA-256：OAEP 的摘要与 MGF1、PSS 的摘要与 MGF1 都固定，避免依赖库默认值。
_OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
_PSS = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH)
_HASH = hashes.SHA256()


def generate_keypair(bits: int = 2048) -> tuple[bytes, bytes]:
    """生成 RSA 密钥对，返回 `(PKCS#8 私钥 PEM, 公钥 PEM)`；只允许 2048/3072。"""
    if bits not in ALLOWED_KEY_SIZES:
        raise ValueError(f"RSA 密钥位数只允许 {sorted(ALLOWED_KEY_SIZES)}，收到 {bits}")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def sign(payload: bytes, private_key_pem: bytes) -> bytes:
    """RSA-PSS(MGF1-SHA256) 签名；`payload` 是待签字节（本项目 = canonical_json 输出）。"""
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload 必须是字节串")
    private_key = serialization.load_pem_private_key(bytes(private_key_pem), password=None)
    return private_key.sign(bytes(payload), _PSS, _HASH)


def verify(payload: bytes, signature: bytes, public_key_pem: bytes) -> bool:
    """验签；畸形签名/错误密钥一律返回 False（与国密层 `verify_bytes` 的语义一致）。"""
    public_key = serialization.load_pem_public_key(bytes(public_key_pem))
    try:
        public_key.verify(bytes(signature), bytes(payload), _PSS, _HASH)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def encrypt_key(cek: bytes, public_key_pem: bytes) -> bytes:
    """RSA-OAEP-SHA256 包裹会话密钥（只接受 1–32B）。"""
    if not 1 <= len(cek) <= 32:
        raise ValueError("RSA-OAEP 只包裹会话密钥（1–32 字节）")
    public_key = serialization.load_pem_public_key(bytes(public_key_pem))
    return public_key.encrypt(bytes(cek), _OAEP)


def decrypt_key(wrapped: bytes, private_key_pem: bytes) -> bytes:
    """解开 OAEP 密文；密文被篡改或私钥不匹配时抛 `ValueError`（不返回 None）。"""
    private_key = serialization.load_pem_private_key(bytes(private_key_pem), password=None)
    try:
        return private_key.decrypt(bytes(wrapped), _OAEP)
    except ValueError as exc:
        # cryptography 对 AEAD 解包失败统一抛 ValueError；这里换成可读文案，不泄露细节
        raise ValueError("RSA-OAEP 解包失败（密文被篡改或私钥不匹配）") from exc