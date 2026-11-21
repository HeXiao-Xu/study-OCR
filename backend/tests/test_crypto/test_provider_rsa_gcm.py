"""S4-9：两套算法实现的同接口参数化测试（`pytest -k "rsa or gcm"` 验收）。

同一组往返/篡改用例同时跑 `SMProvider`（默认国密）与 `RSAProvider`（RSA-OAEP/PSS + AES-GCM），
保证 S6 的性能对比是"同接口换实现"，而不是两套不同形状的 API 各测各的。
"""
from __future__ import annotations

import dataclasses
import secrets

import pytest

from app.crypto import rsa_layer
from app.crypto.provider import (
    AlgoIntegrityError,
    AlgoProvider,
    RSAProvider,
    SMProvider,
    SymmetricPayload,
    get_provider,
)
from tests.test_crypto._keys import EDGE_PRIVATE, EDGE_PUBLIC, VECTOR_PRIVATE, VECTOR_PUBLIC

SM_PAYLOAD = '{"report_no":"RPT-202611-001","total_amount":"1068.00"}'.encode("utf-8")


# ---------------------------------------------------------------- 夹具


@pytest.fixture(scope="module")
def sm_provider() -> SMProvider:
    return SMProvider()


@pytest.fixture(scope="module")
def sm_key():
    return SMProvider.key_from_hex(VECTOR_PRIVATE, VECTOR_PUBLIC)


@pytest.fixture(scope="module")
def sm_other_key():
    return SMProvider.key_from_hex(EDGE_PRIVATE, EDGE_PUBLIC)


@pytest.fixture(scope="module")
def rsa_provider() -> RSAProvider:
    return RSAProvider(key_size=2048)


@pytest.fixture(scope="module")
def rsa_key(rsa_provider: RSAProvider):
    return rsa_provider.keygen()


@pytest.fixture(scope="module")
def rsa_other_key(rsa_provider: RSAProvider):
    return rsa_provider.keygen()


@pytest.fixture(params=["sm", "rsa"], ids=["sm", "rsa"])
def provider_keys(request, sm_provider, sm_key, sm_other_key, rsa_provider, rsa_key, rsa_other_key):
    if request.param == "sm":
        return sm_provider, sm_key, sm_other_key
    return rsa_provider, rsa_key, rsa_other_key


def _flip(data: bytes) -> bytes:
    raw = bytearray(data)
    raw[0] ^= 0x01
    return bytes(raw)


# ---------------------------------------------------------------- 签名


def test_sign_verify_round_trip(provider_keys) -> None:
    provider, key, _ = provider_keys

    signature = provider.sign(SM_PAYLOAD, key)

    assert isinstance(signature, bytes) and signature
    assert provider.verify(SM_PAYLOAD, signature, key) is True


def test_tampered_payload_rejected(provider_keys) -> None:
    provider, key, _ = provider_keys
    signature = provider.sign(SM_PAYLOAD, key)

    assert provider.verify(_flip(SM_PAYLOAD), signature, key) is False


def test_tampered_signature_rejected(provider_keys) -> None:
    provider, key, _ = provider_keys
    signature = provider.sign(SM_PAYLOAD, key)

    assert provider.verify(SM_PAYLOAD, _flip(signature), key) is False


def test_wrong_key_rejected(provider_keys) -> None:
    provider, key, other_key = provider_keys
    signature = provider.sign(SM_PAYLOAD, key)

    assert provider.verify(SM_PAYLOAD, signature, other_key) is False


def test_cross_provider_signatures_do_not_verify(sm_provider, sm_key, rsa_provider, rsa_key) -> None:
    sm_signature = sm_provider.sign(SM_PAYLOAD, sm_key)
    rsa_signature = rsa_provider.sign(SM_PAYLOAD, rsa_key)

    assert rsa_provider.verify(SM_PAYLOAD, sm_signature, rsa_key) is False
    assert sm_provider.verify(SM_PAYLOAD, rsa_signature, sm_key) is False


# ---------------------------------------------------------------- 会话密钥包裹


def test_key_wrap_round_trip(provider_keys) -> None:
    provider, key, _ = provider_keys
    cek = secrets.token_bytes(16)

    wrapped = provider.encrypt_key(cek, key)

    assert wrapped and provider.decrypt_key(wrapped, key) == cek


def _assert_decrypt_rejects_wrong_material(provider, wrapped: bytes, key) -> None:
    """SM2 单靠解包不会发现 C2 篡改（无 C3 比对，D-077），RSA-OAEP 会抛错；两种都算"拿不到正确 CEK"。"""
    cek = bytes(range(16))
    try:
        recovered = provider.decrypt_key(wrapped, key)
    except ValueError:
        return
    assert recovered != cek


def test_key_wrap_tampered_or_wrong_key(provider_keys) -> None:
    provider, key, other_key = provider_keys
    cek = bytes(range(16))
    wrapped = provider.encrypt_key(cek, key)

    _assert_decrypt_rejects_wrong_material(provider, _flip(wrapped), key)
    _assert_decrypt_rejects_wrong_material(provider, wrapped, other_key)


# ---------------------------------------------------------------- 对称层


def test_symmetric_round_trip(provider_keys) -> None:
    provider, _, _ = provider_keys
    cek = secrets.token_bytes(provider.symmetric_key_bytes)
    plaintext = '{"票据":"电子发票","金额":"-100.00"}'.encode("utf-8") + b"x" * 65536

    payload = provider.wrap_symmetric(cek, plaintext, aad=b"header")

    assert isinstance(payload, SymmetricPayload)
    assert provider.unwrap_symmetric(cek, payload, aad=b"header") == plaintext


@pytest.mark.parametrize("field", ["nonce", "ciphertext", "tag"])
def test_symmetric_tamper_rejected(provider_keys, field: str) -> None:
    provider, _, _ = provider_keys
    cek = secrets.token_bytes(provider.symmetric_key_bytes)
    payload = provider.wrap_symmetric(cek, b"integrity matters")
    tampered = dataclasses.replace(payload, **{field: _flip(getattr(payload, field))})

    with pytest.raises(AlgoIntegrityError) as exc:
        provider.unwrap_symmetric(cek, tampered)

    assert exc.value.reason == "mac_mismatch"


def test_symmetric_aad_mismatch_rejected(provider_keys) -> None:
    provider, _, _ = provider_keys
    cek = secrets.token_bytes(provider.symmetric_key_bytes)
    payload = provider.wrap_symmetric(cek, b"payload", aad=b"header-v1")

    with pytest.raises(AlgoIntegrityError):
        provider.unwrap_symmetric(cek, payload, aad=b"header-v2")


def test_symmetric_payload_sizes_and_fresh_nonce(provider_keys) -> None:
    provider, _, _ = provider_keys
    cek = secrets.token_bytes(provider.symmetric_key_bytes)

    first = provider.wrap_symmetric(cek, b"same payload")
    second = provider.wrap_symmetric(cek, b"same payload")

    if provider.name == "sm":
        assert (len(first.nonce), len(first.tag)) == (16, 32)
    else:
        assert (len(first.nonce), len(first.tag)) == (12, 16)
    assert first.nonce != second.nonce


# ---------------------------------------------------------------- provider 工厂与协议


def test_provider_factory_and_protocol(sm_provider, rsa_provider) -> None:
    assert get_provider().name == "sm"
    assert get_provider("sm").name == "sm"
    assert get_provider("RSA").name == "rsa"
    assert isinstance(sm_provider, AlgoProvider)
    assert isinstance(rsa_provider, AlgoProvider)

    with pytest.raises(ValueError):
        get_provider("elgamal")


def test_sm_provider_keygen_is_not_implemented(sm_provider) -> None:
    with pytest.raises(NotImplementedError):
        sm_provider.keygen()


def test_sm_provider_key_from_hex_accepts_prefixed_public_key() -> None:
    key = SMProvider.key_from_hex(VECTOR_PRIVATE, "04" + VECTOR_PUBLIC)

    assert key.algo == "SM2"
    assert len(key.key_fp) == 64


# ---------------------------------------------------------------- RSA 规格


def test_rsa_key_sizes_and_parameters() -> None:
    for bits in (2048, 3072):
        material = RSAProvider(bits).keygen()
        assert material.algo == "RSA"
        assert b"BEGIN PRIVATE KEY" in material.private_key
        assert b"BEGIN PUBLIC KEY" in material.public_key

    with pytest.raises(ValueError):
        RSAProvider(key_size=1024)
    with pytest.raises(ValueError):
        rsa_layer.generate_keypair(1024)


def test_rsa_oaep_rejects_oversized_session_key(rsa_provider, rsa_key) -> None:
    with pytest.raises(ValueError):
        rsa_provider.encrypt_key(b"x" * 200, rsa_key)


def test_rsa_padding_uses_sha256_explicitly(rsa_key) -> None:
    """行为级断言：用"显式 SHA-256 的 PSS/OAEP"独立验一遍，实现若依赖库默认值就会失败。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    payload = b"explicit sha256 check"
    public_key = serialization.load_pem_public_key(rsa_key.public_key)
    private_key = serialization.load_pem_private_key(rsa_key.private_key, password=None)

    signature = rsa_layer.sign(payload, rsa_key.private_key)
    public_key.verify(
        signature,
        payload,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )

    cek = bytes(range(16))
    wrapped = rsa_layer.encrypt_key(cek, rsa_key.public_key)
    recovered = private_key.decrypt(
        wrapped,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    assert recovered == cek