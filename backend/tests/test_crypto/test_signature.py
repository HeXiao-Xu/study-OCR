"""S4-2：签名记录与"规范化 → 签名 → 验签"链路。

记录格式对齐 SCHEMA `signatures`（`algo` / `digest_algo` / `digest_hex` /
`signature_b64` / `cert_id`）与任务卡的 `canonical_payload_base64` / `payload_version`。
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.crypto.canonical import CanonicalJSONError, canonical_json
from app.crypto.signature import (
    SignatureRecord,
    VerifyResult,
    sign_canonical,
    verify_canonical,
    verify_record,
)
from app.crypto.sm.sm3 import sm3_digest
from tests.test_crypto._keys import VECTOR_PRIVATE, VECTOR_PUBLIC

PAYLOAD = {"report_no": "RPT-202611-001", "total_amount": "1068.00", "items": [{"amount": "-100.00"}]}


def _sign(payload: object = PAYLOAD, **overrides: object) -> SignatureRecord:
    kwargs = {"private_key_hex": VECTOR_PRIVATE, "public_key_hex": VECTOR_PUBLIC}
    kwargs.update(overrides)
    return sign_canonical(payload, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 记录内容


def test_sign_canonical_record_fields() -> None:
    record = _sign()

    assert record.algo == "SM2-SM3"
    assert record.digest_algo == "SM3"
    assert record.payload_version == "v1"
    canonical = canonical_json(PAYLOAD)
    assert record.digest_hex == sm3_digest(canonical).hex()
    assert base64.b64decode(record.canonical_payload_base64) == canonical
    assert record.signed_at.tzinfo is not None
    assert record.cert_id is None


def test_sign_canonical_accepts_cert_id_and_payload_version() -> None:
    record = _sign(cert_id=7, payload_version="v2")

    assert record.cert_id == 7
    assert record.payload_version == "v2"


def test_sign_canonical_uses_utc_signed_at() -> None:
    before = datetime.now(timezone.utc)
    record = _sign()
    after = datetime.now(timezone.utc)

    assert before - timedelta(seconds=1) <= record.signed_at <= after + timedelta(seconds=1)
    assert record.signed_at.utcoffset() == timedelta(0)


def test_sign_canonical_rejects_non_canonical_payload() -> None:
    with pytest.raises(CanonicalJSONError):
        _sign({"amount": 1.5})


# ---------------------------------------------------------------- 往返


def test_verify_canonical_round_trip_ok() -> None:
    record = _sign()

    assert verify_record(record, public_key_hex=VECTOR_PUBLIC) is VerifyResult.OK
    assert verify_canonical(PAYLOAD, record, public_key_hex=VECTOR_PUBLIC) is VerifyResult.OK


# ---------------------------------------------------------------- 三类失败


def test_verify_canonical_detects_payload_change() -> None:
    record = _sign()
    tampered = {**PAYLOAD, "total_amount": "9999.00"}

    assert verify_canonical(tampered, record, public_key_hex=VECTOR_PUBLIC) is VerifyResult.DIGEST_MISMATCH
    # 记录本身没被改，单独验记录仍然通过（"载荷被换"只能靠带对象的验签发现）
    assert verify_record(record, public_key_hex=VECTOR_PUBLIC) is VerifyResult.OK


def test_verify_record_detects_payload_and_digest_forgery() -> None:
    """攻击者同时改载荷与 digest_hex：摘要自洽，但签名对不上。"""
    record = _sign()
    forged_canonical = canonical_json({"report_no": "RPT-FORGED"})
    forged = record.model_copy(
        update={
            "canonical_payload_base64": base64.b64encode(forged_canonical).decode("ascii"),
            "digest_hex": sm3_digest(forged_canonical).hex(),
        }
    )

    assert verify_record(forged, public_key_hex=VECTOR_PUBLIC) is VerifyResult.BAD_SIGNATURE


def test_verify_record_detects_tampered_signature() -> None:
    record = _sign()
    signature = base64.b64decode(record.signature_b64)
    tampered = record.model_copy(
        update={
            "signature_b64": base64.b64encode(bytes([signature[0] ^ 0xFF]) + signature[1:]).decode("ascii"),
        }
    )

    assert verify_record(tampered, public_key_hex=VECTOR_PUBLIC) is VerifyResult.BAD_SIGNATURE


def test_verify_record_rejects_wrong_public_key() -> None:
    from tests.test_crypto._keys import EDGE_PUBLIC

    record = _sign()

    assert verify_record(record, public_key_hex=EDGE_PUBLIC) is VerifyResult.BAD_SIGNATURE


def test_verify_record_handles_undecodable_payload_base64() -> None:
    record = _sign()
    broken = record.model_copy(update={"canonical_payload_base64": "not-base64!!"})

    assert verify_record(broken, public_key_hex=VECTOR_PUBLIC) is VerifyResult.DIGEST_MISMATCH


def test_verify_record_handles_undecodable_signature_base64() -> None:
    record = _sign()
    broken = record.model_copy(update={"signature_b64": "not-base64!!"})

    assert verify_record(broken, public_key_hex=VECTOR_PUBLIC) is VerifyResult.BAD_SIGNATURE


# ---------------------------------------------------------------- 模型校验


def test_signature_record_is_frozen() -> None:
    record = _sign()

    with pytest.raises(ValidationError):
        record.digest_hex = "0" * 64  # type: ignore[misc]


def test_signature_record_rejects_unknown_fields() -> None:
    record = _sign()

    with pytest.raises(ValidationError):
        SignatureRecord(**record.model_dump(), unexpected="x")


def test_signature_record_rejects_naive_datetime() -> None:
    payload = _sign().model_dump()
    payload["signed_at"] = datetime(2026, 11, 20, 12, 0, 0)

    with pytest.raises(ValidationError):
        SignatureRecord(**payload)


def test_signature_record_rejects_empty_payload_version() -> None:
    payload = _sign().model_dump()
    payload["payload_version"] = ""

    with pytest.raises(ValidationError):
        SignatureRecord(**payload)


def test_signature_record_rejects_malformed_digest_hex() -> None:
    payload = _sign().model_dump()
    payload["digest_hex"] = "ABC"

    with pytest.raises(ValidationError):
        SignatureRecord(**payload)