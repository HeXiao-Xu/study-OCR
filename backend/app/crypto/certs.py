"""X.509 证书的**运行期**校验（S4-6）。

本模块只做四件事（刻意的能力边界，D-080）：

1. 指纹 `SHA-256(DER)`；
2. 有效期（`not_before` / `not_after`，UTC）；
3. 「证书公钥 == 登记的公钥 hex」（防证书与私钥配错，配错会拖到验签才炸）；
4. 吊销位（由 `certificates.revoked/revoked_at` 传入）。

**不做完整 X.509 链验签**：单 CA 拓扑下，链校验在部署期由 OpenSSL 做一次
（`scripts/openssl.sh verify -CAfile ca.crt user.crt`），运行期只信本地登记表 + 指纹 + 有效期 + 吊销位。
这是任务卡/SCHEMA §2.2 明确的简化，**不适用于多级 CA / 跨机构互信**。

实现注意：
- `cryptography` 能解析证书结构、扩展、DN、有效期，但对 **SM2 公钥**会抛
  `UnsupportedAlgorithm`（它的 `ec` 模块不认 sm2p256v1）；
- 因此公钥从证书 DER 的 `tbsCertificate → subjectPublicKeyInfo → BIT STRING` 取，
  只用 pycryptodomex 的 DER 解码原语，不手写 ASN.1；
- 角色来自 subject 的 `OU`（SCHEMA `certificates` 没有 role 列，`subject_dn` 里带着）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from Cryptodome.Util.asn1 import DerBitString, DerSequence

__all__ = ["CertStatus", "CertificateInfo", "check_certificate", "parse_certificate"]


class CertStatus(StrEnum):
    """证书校验结论（任务卡要求 `cert_revoked`；S5/信封聚合为 `cert_invalid`）。"""

    OK = "ok"
    EXPIRED = "cert_expired"
    NOT_YET_VALID = "cert_not_yet_valid"
    REVOKED = "cert_revoked"
    PUBLIC_KEY_MISMATCH = "cert_public_key_mismatch"
    ROLE_MISMATCH = "cert_role_mismatch"


@dataclass(frozen=True)
class CertificateInfo:
    """证书里我们关心的字段（都是公开信息）。"""

    subject_dn: str
    issuer_dn: str
    serial_no: str  # 小写十六进制（X.509 序列号可达 20 字节，不能用 int 存库）
    fingerprint_sha256: str
    public_key_hex: str  # 128 hex，不带 0x04 前缀
    role: str | None  # 取自 subject 的 OU
    not_before: datetime
    not_after: datetime


def parse_certificate(cert_pem: bytes | str) -> CertificateInfo:
    """解析 PEM 证书；不合法 PEM 抛 `ValueError`。"""
    pem = _as_pem_bytes(cert_pem)
    try:
        cert = x509.load_pem_x509_certificate(pem)
    except ValueError as exc:
        raise ValueError("不是有效的 PEM 证书") from exc

    der = cert.public_bytes(Encoding.DER)
    return CertificateInfo(
        subject_dn=cert.subject.rfc4514_string(),
        issuer_dn=cert.issuer.rfc4514_string(),
        serial_no=format(cert.serial_number, "x"),
        fingerprint_sha256=hashlib.sha256(der).hexdigest(),
        public_key_hex=_public_key_hex_from_cert_der(der),
        role=_role_from_subject(cert),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
    )


def check_certificate(
    cert_pem: bytes | str,
    *,
    expected_public_key_hex: str,
    expect_role: str | None = None,
    revoked: bool = False,
    now: datetime | None = None,
) -> CertStatus:
    """按"吊销 → 有效期 → 公钥一致 → 角色"的顺序判定（已吊销的不再给任何有效理由）。"""
    info = parse_certificate(cert_pem)
    moment = _normalize_now(now)

    if revoked:
        return CertStatus.REVOKED
    if moment < info.not_before:
        return CertStatus.NOT_YET_VALID
    if moment > info.not_after:
        return CertStatus.EXPIRED

    expected = _normalize_public_key_hex(expected_public_key_hex)
    if info.public_key_hex != expected:
        return CertStatus.PUBLIC_KEY_MISMATCH

    if expect_role is not None and info.role != expect_role.strip().lower():
        return CertStatus.ROLE_MISMATCH
    return CertStatus.OK


# ---------------------------------------------------------------- 内部


def _as_pem_bytes(cert_pem: bytes | str) -> bytes:
    if isinstance(cert_pem, bytes):
        return cert_pem
    if isinstance(cert_pem, (bytearray, memoryview)):
        return bytes(cert_pem)
    if isinstance(cert_pem, str):
        try:
            return cert_pem.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("证书 PEM 必须是 ASCII 文本") from exc
    raise TypeError(f"证书必须是 bytes 或 str，收到 {type(cert_pem).__name__}")


def _public_key_hex_from_cert_der(der: bytes) -> str:
    """从 tbsCertificate 取 SubjectPublicKeyInfo 的 BIT STRING → 去掉 04 前缀的公钥 hex。

    `tbsCertificate` 的 SPKI 下标随 version 字段有无而变（v1 无 version 时是第 6 项，v3 是第 7 项），
    这里按"SEQUENCE 且第二项是 BIT STRING"识别，不硬编码下标。
    """
    try:
        certificate = DerSequence()
        certificate.decode(der)
        tbs = DerSequence()
        tbs.decode(certificate[0])
    except (ValueError, TypeError, IndexError) as exc:
        raise ValueError("证书结构不符合预期（无法解析 tbsCertificate）") from exc

    for index in (5, 6):
        if index >= len(tbs):
            continue
        item = tbs[index]
        if not isinstance(item, bytes) or item[:1] != b"\x30":
            continue
        try:
            spki = DerSequence()
            spki.decode(item)
            if len(spki) != 2 or not isinstance(spki[1], bytes) or spki[1][:1] != b"\x03":
                continue
            payload = DerBitString().decode(spki[1]).payload
        except (ValueError, TypeError, IndexError):
            continue
        # BIT STRING：首字节是 unused-bits（0），随后是未压缩点 04‖X‖Y（65 字节）
        if len(payload) == 66 and payload[0] == 0 and payload[1] == 0x04:
            return payload[2:].hex()

    raise ValueError("证书公钥不是未压缩 SM2 点（04‖X‖Y）")


def _role_from_subject(cert: x509.Certificate) -> str | None:
    attributes = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)
    if not attributes:
        return None
    return str(attributes[0].value).strip().lower() or None


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须带时区（统一用 UTC）")
    return now


def _normalize_public_key_hex(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"公钥必须是十六进制字符串，收到 {type(value).__name__}")
    normalized = value.lower()
    if len(normalized) == 130:
        if not normalized.startswith("04"):
            raise ValueError("130 位公钥必须带未压缩点前缀 04")
        normalized = normalized[2:]
    if len(normalized) != 128 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("公钥必须是 128 位十六进制（或 130 位带 04 前缀）")
    return normalized