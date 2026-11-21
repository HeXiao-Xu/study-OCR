"""业务侧密钥材料加载（S5-2）：SM2（keystore JSON）与 RSA（加密 PKCS#8 PEM）。

职责边界：
- 本模块**只做加载与校验**，不生成密钥、不做算法运算（运算在 `crypto/sm/*` 与 `provider.py`）；
- SM 的私钥文件沿用 S4-5 的 `.p8.enc`（PBKDF2 + AES-256-GCM）；
- RSA 的私钥文件是**口令加密的 PKCS#8 PEM**（`cryptography` 的 `BestAvailableEncryption`），
  解出的 PEM 只在内存里，交给 `KeyMaterial`；
- 证书→收方公钥：SM 从证书 DER 的 SPKI 取 128 hex（`certs.py` 已有能力），RSA 取 SPKI PEM；
- 证书校验沿用 D-080 的边界：**不做链验签**，只判有效期、吊销位、公钥一致。

口令错误/文件不可读统一抛 `KeyLoadError("私钥口令错误或密钥不可用")`，
上层转成 `422/42200` 的**统一文案**（不区分"口令错"与"文件坏"，防 oracle）。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from app.crypto.certs import CertStatus, check_certificate, parse_certificate
from app.crypto.keystore import EncryptedPrivateKey, KeystoreError, load_keypair
from app.crypto.provider import KeyMaterial, SMProvider

__all__ = [
    "KeyLoadError",
    "certificate_fingerprint",
    "certificate_problem",
    "load_key_material",
    "recipient_key_material",
]

_UNIFORM_ERROR = "私钥口令错误或密钥不可用"


class KeyLoadError(Exception):
    """密钥不可用（错口令/文件损坏/格式不符）。对外统一文案。"""

    def __init__(self, message: str = _UNIFORM_ERROR) -> None:
        super().__init__(message)
        self.message = message


def load_key_material(
    *, key_algo: str, storage_path: Path, passphrase: str
) -> KeyMaterial:
    """按算法加载私钥材料；SM 走 keystore，RSA 走加密 PEM。"""
    algo = (key_algo or "").strip().upper()
    try:
        if algo == "SM2":
            return _load_sm(storage_path, passphrase)
        if algo == "RSA":
            return _load_rsa(storage_path, passphrase)
    except KeyLoadError:
        raise
    except (OSError, ValueError, TypeError, KeystoreError) as exc:
        # 文件不存在 / JSON 结构坏 / 口令错 / PEM 类型不对，对外表现必须一致
        raise KeyLoadError() from exc
    raise KeyLoadError("不支持的密钥算法（只支持 SM2 / RSA）")


def certificate_fingerprint(cert_pem: str | bytes) -> str:
    """证书 SHA-256 指纹（64 位小写 hex）；SM2/RSA 都能算（certs.parse 只认 SM2）。"""
    cert = _load_certificate(cert_pem)
    return hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()


def recipient_key_material(*, key_algo: str, cert_pem: str | bytes) -> KeyMaterial:
    """从证书取收方公钥材料（只有公钥，`private_key` 为空）。"""
    algo = (key_algo or "").strip().upper()
    if algo == "SM2":
        info = parse_certificate(cert_pem)
        public = info.public_key_hex.encode("ascii")
        return KeyMaterial(
            algo="SM2",
            public_key=public,
            private_key=b"",
            key_fp=hashlib.sha256(b"\x04" + bytes.fromhex(info.public_key_hex)).hexdigest(),
        )
    if algo == "RSA":
        cert = _load_certificate(cert_pem)
        public_key = cert.public_key()
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise KeyLoadError("证书公钥不是 RSA")
        public_pem = public_key.public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        )
        return KeyMaterial(
            algo="RSA",
            public_key=public_pem,
            private_key=b"",
            key_fp=hashlib.sha256(public_pem).hexdigest(),
        )
    raise KeyLoadError("不支持的证书算法（只支持 SM2 / RSA）")


def certificate_problem(
    *,
    key_algo: str,
    cert_pem: str | bytes,
    public_material: KeyMaterial | None,
    revoked: bool = False,
    now: datetime | None = None,
) -> str | None:
    """返回证书问题码（`None` 表示通过）。

    `public_material` 为 None 时只判有效期与吊销（财务收方证书用）；
    给出时额外做"证书公钥 == 密钥公钥"（防私钥与证书配错）。
    """
    algo = (key_algo or "").strip().upper()
    if algo == "SM2":
        if public_material is None:
            # 没有期望公钥时只保留有效期/吊销判定（用证书自身公钥占位）
            expected = parse_certificate(cert_pem).public_key_hex
        else:
            expected = public_material.public_key.decode("ascii")
        status = check_certificate(
            cert_pem,
            expected_public_key_hex=expected,
            revoked=revoked,
            now=now,
        )
        return None if status is CertStatus.OK else status.value

    if algo == "RSA":
        cert = _load_certificate(cert_pem)
        moment = now or datetime.now(timezone.utc)
        if revoked:
            return CertStatus.REVOKED.value
        if moment < cert.not_valid_before_utc:
            return CertStatus.NOT_YET_VALID.value
        if moment > cert.not_valid_after_utc:
            return CertStatus.EXPIRED.value
        if public_material is not None:
            public_key = cert.public_key()
            if not isinstance(public_key, rsa.RSAPublicKey):
                return CertStatus.PUBLIC_KEY_MISMATCH.value
            certificate_public = public_key.public_bytes(
                Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
            )
            if certificate_public != public_material.public_key:
                return CertStatus.PUBLIC_KEY_MISMATCH.value
        return None

    return "unsupported_key_algo"


# ---------------------------------------------------------------- 内部


def _load_sm(path: Path, passphrase: str) -> KeyMaterial:
    record = EncryptedPrivateKey.model_validate_json(path.read_text(encoding="utf-8"))
    pair = load_keypair(record, passphrase)  # KeystoreError → 上层统一文案
    return SMProvider.key_from_hex(pair.private_key_hex, pair.public_key_hex)


def _load_rsa(path: Path, passphrase: str) -> KeyMaterial:
    pem = path.read_bytes()
    key = load_pem_private_key(pem, password=passphrase.encode("utf-8"))
    if not isinstance(key, rsa.RSAPrivateKey):
        raise KeyLoadError("私钥不是 RSA 私钥")
    private_pem = key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    public_pem = key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    return KeyMaterial(
        algo="RSA",
        public_key=public_pem,
        private_key=private_pem,
        key_fp=hashlib.sha256(public_pem).hexdigest(),
    )


def _load_certificate(cert_pem: str | bytes) -> x509.Certificate:
    pem = cert_pem.encode("ascii") if isinstance(cert_pem, str) else bytes(cert_pem)
    try:
        return x509.load_pem_x509_certificate(pem)
    except ValueError as exc:
        raise KeyLoadError("证书不是有效的 PEM") from exc