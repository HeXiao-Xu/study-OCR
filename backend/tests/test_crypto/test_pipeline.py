"""S4-8：密码链路端到端 + 1 bit 篡改矩阵。

链路：`canonical_json → SM3 → SM2 签名 → SM4 数字信封 → 解封 → 验签 → 摘要比对`。

- 签名方 = GB/T 32918.5 A.1 的示例密钥对；
- 收方 = 测试用 SM2 密钥对（公钥 x 以 04 开头，顺带覆盖 gmssl 的 `lstrip("04")` 缺陷路径）；
- 每一类篡改都断言**具体错误归类**，不只断言"失败"。
"""
from __future__ import annotations

import base64
import hashlib

import pytest

from app.crypto.canonical import canonical_json
from app.crypto.envelope import Envelope, EnvelopeError, open_sealed, seal
from app.crypto.signature import (
    SignatureRecord,
    VerifyResult,
    sign_canonical,
    verify_canonical,
    verify_record,
)
from app.crypto.sm.sm3 import sm3_digest
from tests.test_crypto._keys import EDGE_PRIVATE, EDGE_PUBLIC, VECTOR_PRIVATE, VECTOR_PUBLIC

CERT_FP = "a" * 64
PAYLOAD = {
    "report_no": "RPT-202611-001",
    "applicant": "alice",
    "items": [{"invoice_code": "033001", "amount": "100.00"}, {"amount": "968.00"}],
    "total_amount": "1068.00",
    "submitted_at": "2026-11-21T08:00:00+00:00",
}


def _build() -> tuple[bytes, bytes, SignatureRecord, Envelope]:
    canonical = canonical_json(PAYLOAD)
    digest = sm3_digest(canonical)
    record = sign_canonical(PAYLOAD, private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)
    envelope = seal(canonical, recipient_public_key_hex=EDGE_PUBLIC, recipient_cert_fp=CERT_FP)
    return canonical, digest, record, envelope


def _flip_b64(value: str, offset: int = 0) -> str:
    raw = bytearray(base64.b64decode(value))
    raw[offset] ^= 0x01
    return base64.b64encode(bytes(raw)).decode("ascii")


# ---------------------------------------------------------------- 正常链路


def test_full_crypto_pipeline_round_trip() -> None:
    canonical, digest, record, envelope = _build()

    recovered = open_sealed(envelope, private_key_hex=EDGE_PRIVATE)

    assert recovered == canonical
    assert sm3_digest(recovered) == digest
    assert verify_canonical(PAYLOAD, record, public_key_hex=VECTOR_PUBLIC) is VerifyResult.OK
    assert envelope.payload_sha256 == hashlib.sha256(canonical).hexdigest()


def test_pipeline_is_not_self_proving_signature_is_bound_to_payload() -> None:
    """同一签名换一份"长得很像"的载荷必须验不过（防"自己签自己验"的循环自证）。"""
    _, _, record, _ = _build()
    other_payload = {**PAYLOAD, "total_amount": "1068.01"}

    assert verify_canonical(other_payload, record, public_key_hex=VECTOR_PUBLIC) is VerifyResult.DIGEST_MISMATCH
    # 记录本身（未被改）仍然合法：说明失败来自载荷不一致，不是签名坏了
    assert verify_record(record, public_key_hex=VECTOR_PUBLIC) is VerifyResult.OK


# ---------------------------------------------------------------- 篡改矩阵


def test_tampered_payload_detected_by_digest() -> None:
    _, _, record, _ = _build()
    assert verify_canonical({**PAYLOAD, "applicant": "mallory"}, record, public_key_hex=VECTOR_PUBLIC) is (
        VerifyResult.DIGEST_MISMATCH
    )


def test_tampered_signature_bit_detected() -> None:
    _, _, record, _ = _build()
    tampered = record.model_copy(update={"signature_b64": _flip_b64(record.signature_b64, 10)})

    assert verify_record(tampered, public_key_hex=VECTOR_PUBLIC) is VerifyResult.BAD_SIGNATURE


def test_wrong_signature_public_key_detected() -> None:
    _, _, record, _ = _build()

    assert verify_record(record, public_key_hex=EDGE_PUBLIC) is VerifyResult.BAD_SIGNATURE


@pytest.mark.parametrize("field", ["ciphertext_b64", "iv_b64", "mac_b64"])
def test_tampered_envelope_field_fails_mac(field: str) -> None:
    _, _, _, envelope = _build()
    tampered = envelope.model_copy(update={field: _flip_b64(getattr(envelope, field))})

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(tampered, private_key_hex=EDGE_PRIVATE)

    assert exc.value.reason == "mac_mismatch"


@pytest.mark.parametrize("offset", [-1, 70], ids=["c2_section", "c3_section"])
def test_tampered_wrapped_key_fails(offset: int) -> None:
    _, _, _, envelope = _build()
    tampered = envelope.model_copy(update={"wrapped_key_b64": _flip_b64(envelope.wrapped_key_b64, offset)})

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(tampered, private_key_hex=EDGE_PRIVATE)

    assert exc.value.reason in {"mac_mismatch", "bad_envelope"}


def test_wrong_recipient_private_key_fails() -> None:
    _, _, _, envelope = _build()

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(envelope, private_key_hex=VECTOR_PRIVATE)

    assert exc.value.reason in {"mac_mismatch", "bad_envelope"}


def test_tampered_payload_sha256_fails_digest_check() -> None:
    _, _, _, envelope = _build()
    tampered = envelope.model_copy(update={"payload_sha256": "b" * 64})

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(tampered, private_key_hex=EDGE_PRIVATE)

    assert exc.value.reason == "digest_mismatch"