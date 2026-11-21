"""S4-5：SM2 私钥的口令保护（PBKDF2-HMAC-SHA256 + AES-256-GCM）。

测试用的 PKCS#8 由 DER 原语**程序构造**（固定 d，不把 PEM 私钥字面量写进仓库）；
真实 OpenSSL 产出的 PKCS#8 由 `scripts/keygen.py` 的验收跑覆盖。
"""
from __future__ import annotations

import base64

import pytest
from Cryptodome.Util.asn1 import DerObjectId, DerOctetString, DerSequence
from pydantic import ValidationError

from app.crypto.keystore import (
    DEFAULT_ITERATIONS,
    MIN_ITERATIONS,
    EncryptedPrivateKey,
    KeystoreError,
    derive_kek,
    key_fingerprint,
    load_keypair,
    private_key_hex_from_pkcs8,
    unwrap_private_key,
    wrap_private_key,
)
from app.crypto.sm.sm2 import sign_bytes, verify_bytes
from tests.test_crypto._keys import EDGE_PRIVATE, VECTOR_PRIVATE, VECTOR_PUBLIC

PASSPHRASE = "correct-horse-1"


def _build_pkcs8(private_key_hex: str, *, d_bytes_len: int = 32) -> bytes:
    """构造最小但结构合法的 PKCS#8（SEQUENCE{0, AlgorithmIdentifier, OCTET STRING(ECPrivateKey)}）。"""
    d_bytes = bytes.fromhex(private_key_hex)[:d_bytes_len]
    ec_private_key = DerSequence([1, DerOctetString(d_bytes)])
    algorithm = DerSequence(
        [DerObjectId("1.2.840.10045.2.1"), DerObjectId("1.2.156.10197.1.301")]
    )
    return DerSequence([0, algorithm, DerOctetString(ec_private_key.encode())]).encode()


STD_DER = _build_pkcs8(VECTOR_PRIVATE)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _wrap(**overrides: object) -> EncryptedPrivateKey:
    return wrap_private_key(STD_DER, PASSPHRASE, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 往返与解锁


def test_wrap_unwrap_round_trip() -> None:
    record = _wrap()

    assert unwrap_private_key(record, PASSPHRASE) == STD_DER


def test_wrap_uses_documented_defaults() -> None:
    record = _wrap()

    assert record.algo == "SM2"
    assert record.curve == "sm2p256v1"
    assert record.kdf == "PBKDF2-HMAC-SHA256"
    assert record.iterations == DEFAULT_ITERATIONS >= MIN_ITERATIONS
    assert len(base64.b64decode(record.salt_b64)) == 16
    assert len(base64.b64decode(record.nonce_b64)) == 12
    assert len(record.key_fp) == 64


def test_load_keypair_matches_derived_public_key() -> None:
    keypair = load_keypair(_wrap(), PASSPHRASE)

    assert keypair.private_key_hex == VECTOR_PRIVATE.lower()
    assert keypair.public_key_hex == VECTOR_PUBLIC.lower()
    assert keypair.key_fp == key_fingerprint(VECTOR_PUBLIC)


def test_unlocked_key_signs_and_verifies() -> None:
    keypair = load_keypair(_wrap(), PASSPHRASE)

    signature = sign_bytes(
        b"unlocked-signing",
        private_key_hex=keypair.private_key_hex,
        public_key_hex=keypair.public_key_hex,
    )

    assert verify_bytes(b"unlocked-signing", signature, public_key_hex=keypair.public_key_hex) is True


def test_two_wraps_of_same_key_are_independent() -> None:
    first = wrap_private_key(STD_DER, "passphrase-a1")
    second = wrap_private_key(STD_DER, "passphrase-b2")

    assert first.salt_b64 != second.salt_b64
    assert first.ciphertext_b64 != second.ciphertext_b64
    assert unwrap_private_key(first, "passphrase-a1") == STD_DER
    assert unwrap_private_key(second, "passphrase-b2") == STD_DER

    with pytest.raises(KeystoreError):
        unwrap_private_key(first, "passphrase-b2")


# ---------------------------------------------------------------- 失败路径


def test_wrong_passphrase_raises_uniform_error() -> None:
    record = _wrap()

    with pytest.raises(KeystoreError) as exc:
        unwrap_private_key(record, "wrong-pass-11")

    assert str(exc.value) == "口令错误"


def test_tampered_ciphertext_raises_uniform_error() -> None:
    record = _wrap()
    raw = bytearray(base64.b64decode(record.ciphertext_b64))
    raw[0] ^= 0xFF
    tampered = record.model_copy(update={"ciphertext_b64": _b64(bytes(raw))})

    with pytest.raises(KeystoreError) as exc:
        unwrap_private_key(tampered, PASSPHRASE)

    assert str(exc.value) == "口令错误"


@pytest.mark.parametrize("field", ["salt_b64", "nonce_b64"])
def test_tampered_salt_or_nonce_fails(field: str) -> None:
    record = _wrap()
    raw = bytearray(base64.b64decode(getattr(record, field)))
    raw[0] ^= 0xFF
    tampered = record.model_copy(update={field: _b64(bytes(raw))})

    with pytest.raises(KeystoreError):
        unwrap_private_key(tampered, PASSPHRASE)


def test_tampered_iterations_breaks_aad() -> None:
    """把迭代次数改成 1（绕过模型校验）必须解密失败——AAD 绑定了 iterations。"""
    record = _wrap()
    tampered = record.model_copy(update={"iterations": 1})

    with pytest.raises(KeystoreError):
        unwrap_private_key(tampered, PASSPHRASE)


@pytest.mark.parametrize("bad_passphrase", ["", "1234567", "   "])
def test_empty_or_short_passphrase_rejected(bad_passphrase: str) -> None:
    with pytest.raises(ValueError):
        wrap_private_key(STD_DER, bad_passphrase)

    record = _wrap()
    with pytest.raises(ValueError):
        unwrap_private_key(record, bad_passphrase)


def test_iterations_below_minimum_rejected() -> None:
    with pytest.raises(ValueError):
        wrap_private_key(STD_DER, PASSPHRASE, iterations=MIN_ITERATIONS - 1)

    with pytest.raises(ValueError):
        derive_kek(PASSPHRASE, b"\x00" * 16, iterations=1)


@pytest.mark.parametrize(
    "bad_der",
    [
        b"",
        b"not der at all",
        DerSequence([1, 2]).encode(),
    ],
    ids=["empty", "garbage", "wrong_structure"],
)
def test_private_key_parse_rejects_non_pkcs8(bad_der: bytes) -> None:
    with pytest.raises(ValueError):
        private_key_hex_from_pkcs8(bad_der)


def test_private_key_parse_rejects_wrong_scalar_length() -> None:
    with pytest.raises(ValueError):
        private_key_hex_from_pkcs8(_build_pkcs8(VECTOR_PRIVATE, d_bytes_len=31))


# ---------------------------------------------------------------- 模型与指纹


def test_record_model_is_frozen_and_strict() -> None:
    record = _wrap()

    with pytest.raises(ValidationError):
        record.iterations = 1  # type: ignore[misc]

    with pytest.raises(ValidationError):
        EncryptedPrivateKey(**record.model_dump(), unexpected="x")


def test_record_model_validates_iterations_and_fingerprint() -> None:
    data = _wrap().model_dump()

    with pytest.raises(ValidationError):
        EncryptedPrivateKey(**{**data, "iterations": 1000})

    with pytest.raises(ValidationError):
        EncryptedPrivateKey(**{**data, "key_fp": "ABC"})


def test_key_fingerprint_properties() -> None:
    fingerprint = key_fingerprint(VECTOR_PUBLIC)

    assert len(fingerprint) == 64
    assert fingerprint == fingerprint.lower()
    int(fingerprint, 16)
    assert key_fingerprint("04" + VECTOR_PUBLIC.lower()) == fingerprint
    assert key_fingerprint(VECTOR_PUBLIC.upper()) == fingerprint
    assert key_fingerprint(VECTOR_PUBLIC) != key_fingerprint(
        "04b31594867a32f027ed05affea8aa0b0596a846dddb1c1e6cb356beba707a35"
        "069d5ba6c5ac86c1ef2402f9d7c4bf7e01b424e08633eeee8d6fa39e56e98eea"
    )


def test_records_for_different_keys_do_not_interchange() -> None:
    other = wrap_private_key(_build_pkcs8(EDGE_PRIVATE), PASSPHRASE)

    with pytest.raises(KeystoreError):
        load_keypair(other, "another-pass-3")

# ---------------------------------------------------------------- 错误分支（覆盖率）


def test_derive_kek_rejects_bad_salt_and_types() -> None:
    with pytest.raises(TypeError):
        derive_kek(PASSPHRASE, "not-bytes")  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        derive_kek(PASSPHRASE, b"short")

    with pytest.raises(TypeError):
        derive_kek(PASSPHRASE, b"\x00" * 16, iterations=True)  # type: ignore[arg-type]


def test_pkcs8_parse_rejects_wrong_ec_version_and_zero_scalar() -> None:
    from Cryptodome.Util.asn1 import DerObjectId, DerOctetString, DerSequence

    algorithm = DerSequence(
        [DerObjectId("1.2.840.10045.2.1"), DerObjectId("1.2.156.10197.1.301")]
    )
    bad_version = DerSequence([2, DerOctetString(bytes.fromhex(VECTOR_PRIVATE))])
    wrong_der = DerSequence([0, algorithm, DerOctetString(bad_version.encode())]).encode()

    with pytest.raises(ValueError):
        private_key_hex_from_pkcs8(wrong_der)

    with pytest.raises(ValueError):
        private_key_hex_from_pkcs8(_build_pkcs8("00" * 32))


def test_wrap_rejects_empty_or_non_bytes_der() -> None:
    with pytest.raises(ValueError):
        wrap_private_key(b"", PASSPHRASE)

    with pytest.raises(TypeError):
        wrap_private_key("not-bytes", PASSPHRASE)  # type: ignore[arg-type]


def test_unwrap_rejects_wrong_record_type() -> None:
    with pytest.raises(TypeError):
        unwrap_private_key("not-a-record", PASSPHRASE)  # type: ignore[arg-type]


def test_unwrap_rejects_bad_salt_length_and_bad_base64() -> None:
    record = _wrap()

    with pytest.raises(KeystoreError):
        unwrap_private_key(record.model_copy(update={"salt_b64": _b64(b"short")}), PASSPHRASE)

    with pytest.raises(KeystoreError):
        unwrap_private_key(record.model_copy(update={"salt_b64": "!!!"}), PASSPHRASE)


def test_passphrase_must_be_str() -> None:
    with pytest.raises(TypeError):
        wrap_private_key(STD_DER, b"bytes-pass-123")  # type: ignore[arg-type]


def test_key_fingerprint_validation_errors() -> None:
    with pytest.raises(TypeError):
        key_fingerprint(123)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        key_fingerprint("a" * 130)  # 130 位但不是 04 开头

    with pytest.raises(ValueError):
        key_fingerprint("zz" * 64)


def test_wrap_accepts_bytearray_der() -> None:
    record = wrap_private_key(bytearray(STD_DER), PASSPHRASE)

    assert unwrap_private_key(record, PASSPHRASE) == STD_DER
