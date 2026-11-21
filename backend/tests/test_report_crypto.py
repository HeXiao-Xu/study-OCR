"""S5-2 纯函数测试：签名域拼装、EnvelopeV1 封装/拆封、密钥与证书加载（默认套件，无 DB）。

SM 的**私钥文件加载**放在 `test_submit.py`（那里有 Docker 现场生成的 `.p8.enc`）；
本文件用 GB/T 32918.5 的公开测试密钥对（`tests/test_crypto/_keys.py`）验证算法编排本身。
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.crypto.certs import parse_certificate
from app.crypto.key_loader import (
    KeyLoadError,
    certificate_problem,
    load_key_material,
    recipient_key_material,
)
from app.crypto.provider import RSAProvider, SMProvider
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.report_items import ReportItem
from app.models.signatures import Signature
from app.services.report_crypto import (
    EnvelopeOpenError,
    SubmitItem,
    algo_names,
    build_sealed_report,
    build_signing_payload,
    canonical_signing_bytes,
    open_sealed,
    seal_payload,
)
from tests.test_crypto._keys import VECTOR_PRIVATE, VECTOR_PUBLIC

FIXTURES = Path(__file__).resolve().parent / "test_crypto" / "fixtures"


# --------------------------------------------------------------------------- #
# 构造器（transient ORM 对象，不落库）
# --------------------------------------------------------------------------- #
def _item(invoice_id: int, *, amount: str = "60.00", sha: str = "a" * 64) -> SubmitItem:
    return SubmitItem(
        item=ReportItem(form_id=1, invoice_id=invoice_id, amount=Decimal(amount)),
        invoice=Invoice(
            id=invoice_id,
            invoice_code="1234567890",
            invoice_no=f"{invoice_id:08d}",
            amount=Decimal("60.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal(amount),
        ),
        file=InvoiceFile(
            id=invoice_id,
            filename="a.jpg",
            mime="image/jpeg",
            size=3,
            sha256=sha,
            storage_key=f"invoices/x/{invoice_id}.bin",
            is_cipher=False,
            page_count=1,
        ),
    )


def _form(**overrides) -> ReportForm:
    values = {
        "id": 1,
        "report_no": "RPT-202611-0001",
        "applicant_id": 7,
        "title": "2026-11 差旅报销",
        "reason": "学术会议",
        "total_amount": Decimal("120.00"),
        "item_count": 2,
        "status": "draft",
        "created_at": datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ReportForm(**values)


def _signature() -> Signature:
    return Signature(
        form_id=1,
        signer_id=7,
        algo="SM2",
        digest_algo="SM3",
        digest_hex="b" * 64,
        signature_b64="c2ln",
        cert_id=3,
        version=1,
        status="active",
    )


# --------------------------------------------------------------------------- #
# 签名域
# --------------------------------------------------------------------------- #
def test_signing_payload_is_deterministic() -> None:
    form = _form()
    items = [_item(2), _item(1)]

    first = build_signing_payload(form, items, nonce="0" * 32)
    second = build_signing_payload(form, list(reversed(items)), nonce="0" * 32)

    assert first == second
    # items 按 invoice_id 升序，金额是字符串（canonical 拒 Decimal/float）
    assert [row["invoice_no"] for row in first["items"]] == ["00000001", "00000002"]
    assert first["total_amount"] == "120.00"
    assert first["items"][0]["amount"] == "60.00"
    assert first["items"][0]["file_sha256"] == "a" * 64
    assert canonical_signing_bytes(form, items, nonce="0" * 32) == canonical_signing_bytes(
        form, list(reversed(items)), nonce="0" * 32
    )


def test_signing_payload_nonce_changes_bytes() -> None:
    form, items = _form(), [_item(1)]
    a = canonical_signing_bytes(form, items, nonce="a" * 32)
    b = canonical_signing_bytes(form, items, nonce="b" * 32)
    assert a != b
    assert b'"nonce":"' + b"b" * 32 in b


def test_sealed_report_contains_fields_and_signature_metadata() -> None:
    form = _form()
    items = [_item(1)]
    sealed = build_sealed_report(
        form,
        items,
        applicant_username="alice",
        applicant_dept="计算机学院",
        joined_fields={1: {"total_amount": "60.00"}},
        signature=_signature(),
        signature_cert_fp="d" * 64,
        submitted_at=datetime(2026, 11, 21, 11, 0, tzinfo=timezone.utc),
    )
    assert sealed["payload_version"] == "report-v1"
    assert sealed["applicant"]["username"] == "alice"
    assert sealed["items"][0]["fields"] == {"total_amount": "60.00"}
    assert sealed["signature"]["digest_hex"] == "b" * 64
    assert sealed["submitted_at"].startswith("2026-11-21T11:00:00")


# --------------------------------------------------------------------------- #
# EnvelopeV1：SM4 + SM2（公开测试密钥对）
# --------------------------------------------------------------------------- #
def _sm_recipient():
    provider = SMProvider()
    key = SMProvider.key_from_hex(VECTOR_PRIVATE, VECTOR_PUBLIC)
    return provider, key


def _flip_b64(value: str, index: int = 0) -> str:
    raw = bytearray(base64.b64decode(value, validate=True))
    raw[index] ^= 0x01
    return base64.b64encode(bytes(raw)).decode("ascii")


def test_seal_open_roundtrip_sm() -> None:
    provider, key = _sm_recipient()
    plaintext = "票据字段 JSON".encode("utf-8")

    sealed = seal_payload(
        provider=provider, recipient=key, recipient_cert_fp="e" * 64, plaintext=plaintext
    )

    assert len(base64.b64decode(sealed.iv_b64)) == 16
    assert len(base64.b64decode(sealed.mac_b64)) == 32
    assert sealed.payload_sha256 != ""
    opened = open_sealed(
        provider=provider,
        private_key=key,
        key_algo=sealed.key_algo,
        cipher_algo=sealed.cipher_algo,
        recipient_cert_fp="e" * 64,
        wrapped_key_b64=sealed.wrapped_key_b64,
        iv_b64=sealed.iv_b64,
        mac_b64=sealed.mac_b64,
        ciphertext=sealed.ciphertext,
        payload_sha256=sealed.payload_sha256,
    )
    assert opened == plaintext


def test_seal_is_randomized_sm() -> None:
    provider, key = _sm_recipient()
    first = seal_payload(provider=provider, recipient=key, recipient_cert_fp="e" * 64, plaintext=b"x")
    second = seal_payload(provider=provider, recipient=key, recipient_cert_fp="e" * 64, plaintext=b"x")
    assert (first.iv_b64, first.wrapped_key_b64, first.ciphertext) != (
        second.iv_b64,
        second.wrapped_key_b64,
        second.ciphertext,
    )


@pytest.mark.parametrize(
    "field",
    ["wrapped_key_b64", "iv_b64", "mac_b64", "recipient_cert_fp", "payload_sha256"],
)
def test_envelope_tampering_fails_sm(field: str) -> None:
    provider, key = _sm_recipient()
    sealed = seal_payload(provider=provider, recipient=key, recipient_cert_fp="e" * 64, plaintext=b"payload")
    kwargs = dict(
        provider=provider,
        private_key=key,
        key_algo=sealed.key_algo,
        cipher_algo=sealed.cipher_algo,
        recipient_cert_fp="e" * 64,
        wrapped_key_b64=sealed.wrapped_key_b64,
        iv_b64=sealed.iv_b64,
        mac_b64=sealed.mac_b64,
        ciphertext=sealed.ciphertext,
        payload_sha256=sealed.payload_sha256,
    )
    kwargs[field] = _flip_b64(kwargs[field]) if field.endswith("_b64") else "f" * 64
    with pytest.raises(EnvelopeOpenError):
        open_sealed(**kwargs)


def test_envelope_wrong_private_key_fails_sm() -> None:
    provider, key = _sm_recipient()
    other = SMProvider.key_from_hex(
        "7F3A9C41E6B2D085A3F19C7E52B8D46A0C9E1F3B7D5A2C8E6F4B1D9A3C7E6146",
        "04b31594867a32f027ed05affea8aa0b0596a846dddb1c1e6cb356beba707a35"
        "069d5ba6c5ac86c1ef2402f9d7c4bf7e01b424e08633eeee8d6fa39e56e98eea",
    )
    sealed = seal_payload(provider=provider, recipient=key, recipient_cert_fp="e" * 64, plaintext=b"p")
    with pytest.raises(EnvelopeOpenError):
        open_sealed(
            provider=provider,
            private_key=other,
            key_algo=sealed.key_algo,
            cipher_algo=sealed.cipher_algo,
            recipient_cert_fp="e" * 64,
            wrapped_key_b64=sealed.wrapped_key_b64,
            iv_b64=sealed.iv_b64,
            mac_b64=sealed.mac_b64,
            ciphertext=sealed.ciphertext,
            payload_sha256=sealed.payload_sha256,
        )


# --------------------------------------------------------------------------- #
# EnvelopeV1：AES-GCM + RSA-OAEP（对照实现）
# --------------------------------------------------------------------------- #
def test_seal_open_roundtrip_rsa() -> None:
    provider = RSAProvider(2048)
    key = provider.keygen()

    sealed = seal_payload(
        provider=provider, recipient=key, recipient_cert_fp="e" * 64, plaintext=b"rsa payload"
    )

    assert len(base64.b64decode(sealed.iv_b64)) == 12
    assert len(base64.b64decode(sealed.mac_b64)) == 16
    assert (sealed.key_algo, sealed.cipher_algo) == ("RSA-OAEP", "AES-256-GCM")
    opened = open_sealed(
        provider=provider,
        private_key=key,
        key_algo=sealed.key_algo,
        cipher_algo=sealed.cipher_algo,
        recipient_cert_fp="e" * 64,
        wrapped_key_b64=sealed.wrapped_key_b64,
        iv_b64=sealed.iv_b64,
        mac_b64=sealed.mac_b64,
        ciphertext=sealed.ciphertext,
        payload_sha256=sealed.payload_sha256,
    )
    assert opened == b"rsa payload"


def test_envelope_tampering_fails_rsa() -> None:
    provider = RSAProvider(2048)
    key = provider.keygen()
    sealed = seal_payload(provider=provider, recipient=key, recipient_cert_fp="e" * 64, plaintext=b"p")
    with pytest.raises(EnvelopeOpenError):
        open_sealed(
            provider=provider,
            private_key=key,
            key_algo=sealed.key_algo,
            cipher_algo=sealed.cipher_algo,
            recipient_cert_fp="e" * 64,
            wrapped_key_b64=_flip_b64(sealed.wrapped_key_b64),
            iv_b64=sealed.iv_b64,
            mac_b64=sealed.mac_b64,
            ciphertext=sealed.ciphertext,
            payload_sha256=sealed.payload_sha256,
        )


# --------------------------------------------------------------------------- #
# 密钥与证书加载
# --------------------------------------------------------------------------- #
def _write_encrypted_rsa_pem(tmp_path: Path, passphrase: str = "test-pass-1234") -> Path:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
    )
    path = tmp_path / "rsa-sign.pem"
    path.write_bytes(pem)
    return path


def test_load_rsa_key_material_and_wrong_passphrase(tmp_path: Path) -> None:
    path = _write_encrypted_rsa_pem(tmp_path)
    material = load_key_material(
        key_algo="RSA", storage_path=path, passphrase="test-pass-1234"
    )
    assert material.algo == "RSA"
    assert b"PRIVATE KEY" in material.private_key
    with pytest.raises(KeyLoadError):
        load_key_material(key_algo="RSA", storage_path=path, passphrase="wrong-pass")


def test_certificate_problem_sm_fixture() -> None:
    cert_pem = (FIXTURES / "alice.crt.pem").read_bytes()
    info = parse_certificate(cert_pem)

    assert certificate_problem(
        key_algo="SM2", cert_pem=cert_pem, public_material=None
    ) is None
    # 期望公钥不一致 → 公钥绑定失败
    wrong = recipient_key_material(key_algo="SM2", cert_pem=(FIXTURES / "bob.crt.pem").read_bytes())
    assert (
        certificate_problem(key_algo="SM2", cert_pem=cert_pem, public_material=wrong)
        == "cert_public_key_mismatch"
    )
    # 吊销优先于其它判定
    assert (
        certificate_problem(
            key_algo="SM2", cert_pem=cert_pem, public_material=None, revoked=True
        )
        == "cert_revoked"
    )
    assert info.public_key_hex


def _self_signed_rsa_cert(tmp_path: Path, suffix: str = "") -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "finance-test")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / f"rsa-cert{suffix}.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path = tmp_path / f"rsa-key{suffix}.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"test-pass-1234"),
        )
    )
    return cert_path, key_path


def test_certificate_problem_rsa_pairing(tmp_path: Path) -> None:
    cert_path, key_path = _self_signed_rsa_cert(tmp_path)
    cert_pem = cert_path.read_bytes()
    material = load_key_material(
        key_algo="RSA", storage_path=key_path, passphrase="test-pass-1234"
    )
    assert certificate_problem(
        key_algo="RSA", cert_pem=cert_pem, public_material=material
    ) is None

    other_cert, _ = _self_signed_rsa_cert(tmp_path, suffix="-other")
    assert (
        certificate_problem(
            key_algo="RSA", cert_pem=other_cert.read_bytes(), public_material=material
        )
        == "cert_public_key_mismatch"
    )


def test_algo_names_match_schema_enums() -> None:
    assert algo_names(SMProvider()).signature_algo == "SM2"
    rsa_names = algo_names(RSAProvider())
    assert (rsa_names.signature_algo, rsa_names.envelope_key_algo, rsa_names.cipher_algo) == (
        "RSA-PSS",
        "RSA-OAEP",
        "AES-256-GCM",
    )