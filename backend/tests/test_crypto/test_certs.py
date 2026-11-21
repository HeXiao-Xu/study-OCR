"""S4-6：自建 CA 与角色证书的运行期校验。

夹具是**预生成的公开证书**（不含私钥），由 `scripts/ca_init.sh` 的同一套 OpenSSL 命令生成；
下面的指纹/序列号/公钥常量来自生成时的 `openssl x509 -noout -subject -serial -fingerprint -sha256 -dates`。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID

from app.crypto.certs import CertStatus, check_certificate, parse_certificate

FIXTURES = Path(__file__).parent / "fixtures"
CA_PEM = (FIXTURES / "ca.crt.pem").read_bytes()
ALICE_PEM = (FIXTURES / "alice.crt.pem").read_bytes()
BOB_PEM = (FIXTURES / "bob.crt.pem").read_bytes()

CA_FINGERPRINT = "4e312c9788bc1cb6c3a991b7d4356700be6f567dc5d8c3070fb532ca69a1d926"
CA_SERIAL = "4b6487c628a7a6b868f3f629dc4031943901e1da"

ALICE_FINGERPRINT = "9636254fcc0b53876bca02cc764d958f677683968458555e2c9f1ab4c44e8ba0"
ALICE_SERIAL = "5175644e0dc6b71d510945365433bd8fdb0c1854"
ALICE_NOT_BEFORE = datetime(2026, 11, 21, 7, 3, 23, tzinfo=timezone.utc)
ALICE_NOT_AFTER = datetime(2036, 11, 18, 7, 3, 23, tzinfo=timezone.utc)
ALICE_PUBLIC_KEY_HEX = (
    "f421d5aa71df3e59d903fd3b5217989f2d921eeb10e58bff29574bea295c973"
    "10ccc795215c262aae1dce31e4e67684e04ada49006240568db04533b3135ef43"
)

BOB_FINGERPRINT = "4647f3645bc3f5fa45062b8aa93a2dda3eca6f8b540c2bbd4a43a3ad36938cf9"
BOB_PUBLIC_KEY_HEX = (
    "004e07739c96a828df462b1dff99ce6afa6640b35a2ea5cb6dd0789db6a787"
    "8e490464362d9712460362829155b5512f528652d9f794b626225413437c6521cd"
)


# ---------------------------------------------------------------- 解析


def test_parse_certificate_fields() -> None:
    info = parse_certificate(ALICE_PEM)

    assert "CN=alice" in info.subject_dn
    assert "OU=submitter" in info.subject_dn
    assert "CN=StudyOCR-CA" in info.issuer_dn
    assert info.serial_no == ALICE_SERIAL
    assert info.role == "submitter"
    assert info.not_before == ALICE_NOT_BEFORE
    assert info.not_after == ALICE_NOT_AFTER


def test_parse_certificate_fingerprint_matches_openssl() -> None:
    info = parse_certificate(ALICE_PEM)

    assert info.fingerprint_sha256 == ALICE_FINGERPRINT
    cert = x509.load_pem_x509_certificate(ALICE_PEM)
    assert info.fingerprint_sha256 == cert.fingerprint(hashes.SHA256()).hex()


def test_parse_certificate_public_key_hex_matches_pem() -> None:
    info = parse_certificate(ALICE_PEM)

    assert info.public_key_hex == ALICE_PUBLIC_KEY_HEX
    assert parse_certificate(BOB_PEM).public_key_hex == BOB_PUBLIC_KEY_HEX

    # cryptography 不支持 SM2 公钥，必须走证书 DER 的 SPKI 解析（certs.py 的实现依据）
    with pytest.raises(UnsupportedAlgorithm):
        x509.load_pem_x509_certificate(ALICE_PEM).public_key()


def test_certificate_extensions_match_task_card() -> None:
    cert = x509.load_pem_x509_certificate(ALICE_PEM)
    key_usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    extended = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value

    assert key_usage.digital_signature is True
    assert key_usage.content_commitment is True  # nonRepudiation
    assert key_usage.key_encipherment is True
    assert ExtendedKeyUsageOID.CLIENT_AUTH in extended
    assert cert.signature_algorithm_oid.dotted_string == "1.2.156.10197.1.501"  # sm2sign-with-sm3

    ca_cert = x509.load_pem_x509_certificate(CA_PEM)
    ca_basic = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert ca_basic.ca is True
    ca_usage = ca_cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ca_usage.key_cert_sign is True


def test_parse_ca_certificate_has_no_role() -> None:
    info = parse_certificate(CA_PEM)

    assert info.role is None
    assert info.fingerprint_sha256 == CA_FINGERPRINT
    assert info.serial_no == CA_SERIAL


# ---------------------------------------------------------------- 校验结论


def test_check_certificate_ok() -> None:
    assert (
        check_certificate(
            ALICE_PEM,
            expected_public_key_hex=ALICE_PUBLIC_KEY_HEX,
            expect_role="submitter",
        )
        is CertStatus.OK
    )


def test_check_certificate_expired_and_not_yet_valid() -> None:
    assert (
        check_certificate(
            ALICE_PEM,
            expected_public_key_hex=ALICE_PUBLIC_KEY_HEX,
            now=ALICE_NOT_AFTER + timedelta(seconds=1),
        )
        is CertStatus.EXPIRED
    )
    assert (
        check_certificate(
            ALICE_PEM,
            expected_public_key_hex=ALICE_PUBLIC_KEY_HEX,
            now=ALICE_NOT_BEFORE - timedelta(seconds=1),
        )
        is CertStatus.NOT_YET_VALID
    )


def test_check_certificate_revoked_wins_over_everything() -> None:
    assert (
        check_certificate(
            ALICE_PEM,
            expected_public_key_hex=ALICE_PUBLIC_KEY_HEX,
            expect_role="submitter",
            revoked=True,
        )
        is CertStatus.REVOKED
    )


def test_check_certificate_detects_key_mismatch() -> None:
    """证书与私钥配错：拿 bob 的公钥验 alice 的证书必须失败。"""
    assert (
        check_certificate(ALICE_PEM, expected_public_key_hex=BOB_PUBLIC_KEY_HEX)
        is CertStatus.PUBLIC_KEY_MISMATCH
    )


def test_check_certificate_accepts_prefixed_public_key() -> None:
    assert (
        check_certificate(ALICE_PEM, expected_public_key_hex="04" + ALICE_PUBLIC_KEY_HEX)
        is CertStatus.OK
    )


def test_check_certificate_detects_role_mismatch() -> None:
    assert (
        check_certificate(
            ALICE_PEM,
            expected_public_key_hex=ALICE_PUBLIC_KEY_HEX,
            expect_role="reviewer",
        )
        is CertStatus.ROLE_MISMATCH
    )


# ---------------------------------------------------------------- 非法输入


@pytest.mark.parametrize(
    "bad",
    [b"", b"not a certificate", b"\x30\x03\x02\x01\x01"],
    ids=["empty", "garbage", "der_not_pem"],
)
def test_parse_certificate_rejects_invalid_input(bad: bytes) -> None:
    with pytest.raises(ValueError):
        parse_certificate(bad)


def test_check_certificate_requires_timezone_aware_now() -> None:
    with pytest.raises(ValueError):
        check_certificate(
            ALICE_PEM,
            expected_public_key_hex=ALICE_PUBLIC_KEY_HEX,
            now=datetime(2027, 1, 1, 0, 0, 0),
        )