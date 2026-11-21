"""S4-4：数字信封（SM2 包裹 CEK + SM4-CBC Encrypt-then-MAC）。"""
from __future__ import annotations

import base64
import hashlib
import time
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.crypto.envelope import Envelope, EnvelopeError, open_sealed, seal
from tests.test_crypto._keys import EDGE_PRIVATE, VECTOR_PRIVATE, VECTOR_PUBLIC

CERT_FP = "a" * 64
PAYLOAD = '{"report_no":"RPT-202611-001","total_amount":"1068.00"}'.encode("utf-8")


def _seal(plaintext: bytes = PAYLOAD, **overrides: object) -> Envelope:
    kwargs = {"recipient_public_key_hex": VECTOR_PUBLIC, "recipient_cert_fp": CERT_FP}
    kwargs.update(overrides)
    return seal(plaintext, **kwargs)  # type: ignore[arg-type]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------- 往返与字段


def test_seal_open_round_trip() -> None:
    envelope = _seal()

    assert open_sealed(envelope, private_key_hex=VECTOR_PRIVATE) == PAYLOAD
    assert envelope.cipher_mode == "SM4-CBC"
    assert envelope.key_algo == "SM2"
    assert envelope.payload_sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert len(base64.b64decode(envelope.iv_b64)) == 16
    assert len(base64.b64decode(envelope.mac_b64)) == 32
    # C1(64) ‖ C3(32) ‖ C2(16)，C1 不带 04 前缀（SCHEMA 注释"约 112 字节"）
    assert len(base64.b64decode(envelope.wrapped_key_b64)) == 112
    assert envelope.recipient_cert_fp == CERT_FP
    assert envelope.created_at.tzinfo is not None


def test_seal_uses_fresh_cek_and_iv_each_time() -> None:
    first = _seal()
    second = _seal()

    for field in ("iv_b64", "ciphertext_b64", "mac_b64", "wrapped_key_b64"):
        assert getattr(first, field) != getattr(second, field)


def test_seal_open_round_trip_empty_and_large_payload() -> None:
    for payload in (b"", b"x", b"y" * 1024):
        envelope = _seal(payload)
        assert open_sealed(envelope, private_key_hex=VECTOR_PRIVATE) == payload


def test_seal_accepts_payload_version_and_detects_change() -> None:
    envelope = _seal(payload_version="v2")

    assert open_sealed(envelope, private_key_hex=VECTOR_PRIVATE) == PAYLOAD

    swapped = envelope.model_copy(update={"payload_version": "v3"})
    with pytest.raises(EnvelopeError) as exc:
        open_sealed(swapped, private_key_hex=VECTOR_PRIVATE)
    assert exc.value.reason == "mac_mismatch"


# ---------------------------------------------------------------- 篡改（每个字段都要被绑定）


@pytest.mark.parametrize(
    "field",
    ["ciphertext_b64", "iv_b64", "mac_b64"],
    ids=["ciphertext", "iv", "mac"],
)
def test_open_sealed_detects_tampered_sm4_fields(field: str) -> None:
    envelope = _seal()
    raw = bytearray(base64.b64decode(getattr(envelope, field)))
    raw[0] ^= 0xFF
    tampered = envelope.model_copy(update={field: _b64(bytes(raw))})

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(tampered, private_key_hex=VECTOR_PRIVATE)
    assert exc.value.reason == "mac_mismatch"


def test_open_sealed_detects_tampered_wrapped_key_payload_section() -> None:
    envelope = _seal()
    raw = bytearray(base64.b64decode(envelope.wrapped_key_b64))
    raw[-1] ^= 0x01  # C2 段：CEK 变 → MAC key 变
    tampered = envelope.model_copy(update={"wrapped_key_b64": _b64(bytes(raw))})

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(tampered, private_key_hex=VECTOR_PRIVATE)
    assert exc.value.reason == "mac_mismatch"


def test_open_sealed_detects_tampered_wrapped_key_c3_section() -> None:
    """C3 段被改时 CEK 不变，必须靠"wrapped_key 进 MAC"发现（D-077）。"""
    envelope = _seal()
    raw = bytearray(base64.b64decode(envelope.wrapped_key_b64))
    raw[70] ^= 0x01  # C1 之后、C2 之前，正好落在 C3 段
    tampered = envelope.model_copy(update={"wrapped_key_b64": _b64(bytes(raw))})

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(tampered, private_key_hex=VECTOR_PRIVATE)
    assert exc.value.reason == "mac_mismatch"


def test_open_sealed_detects_tampered_cert_fingerprint() -> None:
    envelope = _seal()
    tampered = envelope.model_copy(update={"recipient_cert_fp": "b" * 64})

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(tampered, private_key_hex=VECTOR_PRIVATE)
    assert exc.value.reason == "mac_mismatch"


def test_open_sealed_detects_payload_sha_mismatch() -> None:
    envelope = _seal()
    tampered = envelope.model_copy(update={"payload_sha256": "b" * 64})

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(tampered, private_key_hex=VECTOR_PRIVATE)
    assert exc.value.reason == "digest_mismatch"


def test_open_sealed_rejects_wrong_private_key() -> None:
    envelope = _seal()

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(envelope, private_key_hex=EDGE_PRIVATE)
    assert exc.value.reason in {"mac_mismatch", "bad_envelope"}


# ---------------------------------------------------------------- 结构错误


@pytest.mark.parametrize(
    "field",
    ["iv_b64", "ciphertext_b64", "mac_b64", "wrapped_key_b64"],
    ids=["iv", "ciphertext", "mac", "wrapped_key"],
)
def test_open_sealed_rejects_invalid_base64(field: str) -> None:
    envelope = _seal()
    tampered = envelope.model_copy(update={field: "not-base64!!"})

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(tampered, private_key_hex=VECTOR_PRIVATE)
    assert exc.value.reason == "bad_envelope"


def test_open_sealed_rejects_bad_lengths() -> None:
    envelope = _seal()

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(envelope.model_copy(update={"iv_b64": _b64(b"short")}), private_key_hex=VECTOR_PRIVATE)
    assert exc.value.reason == "bad_envelope"

    with pytest.raises(EnvelopeError) as exc:
        open_sealed(envelope.model_copy(update={"wrapped_key_b64": _b64(b"short")}), private_key_hex=VECTOR_PRIVATE)
    assert exc.value.reason == "bad_envelope"


# ---------------------------------------------------------------- 模型校验


def test_envelope_model_is_frozen_and_rejects_unknown_fields() -> None:
    envelope = _seal()

    with pytest.raises(ValidationError):
        envelope.iv_b64 = "x"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        Envelope(**envelope.model_dump(), unexpected="x")


def test_envelope_model_validates_fingerprint_and_timestamp() -> None:
    data = _seal().model_dump()

    with pytest.raises(ValidationError):
        Envelope(**{**data, "recipient_cert_fp": "ABC"})

    with pytest.raises(ValidationError):
        Envelope(**{**data, "created_at": datetime(2026, 11, 21, 12, 0, 0)})


# ---------------------------------------------------------------- 性能（任务卡验收）


def test_2mb_seal_open_under_one_second() -> None:
    payload = b"\x00" * (2 * 1024 * 1024)

    started = time.perf_counter()
    envelope = _seal(payload)
    recovered = open_sealed(envelope, private_key_hex=VECTOR_PRIVATE)
    elapsed = time.perf_counter() - started

    assert recovered == payload
    assert elapsed < 1.0, f"2MB seal+open 用了 {elapsed:.3f}s（任务卡要求 <1s）"