"""S4-3：SM4-CBC + HMAC-SM3 的 Encrypt-then-MAC 对称层。

国标向量：GB/T 32907-2016 附录 A.1（ECB 单块）。
CBC 交叉向量：OpenSSL 3（Node v25 `createCipheriv('sm4-cbc')`）独立计算。
"""
from __future__ import annotations

import pytest

from app.crypto.sm.sm3 import hmac_sm3
from app.crypto.sm.sm4 import (
    BLOCK_SIZE,
    IntegrityError,
    Sm4Ciphertext,
    _cbc_encrypt,
    _encrypt_block,
    _pkcs7_pad,
    decrypt,
    derive_mac_key,
    encrypt,
)

KEY = bytes.fromhex("0123456789abcdeffedcba9876543210")
FIXED_IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")


def _seal(plaintext: bytes, *, key: bytes = KEY, aad: bytes = b"") -> Sm4Ciphertext:
    return encrypt(key, plaintext, aad=aad)


# ---------------------------------------------------------------- 国标 / 交叉向量


def test_encrypt_block_matches_gb_t_32907_vector() -> None:
    block = bytes.fromhex("0123456789abcdeffedcba9876543210")

    assert _encrypt_block(KEY, block).hex() == "681edf34d206965e86b3e94f536e4246"


def test_cbc_matches_openssl_cross_vector() -> None:
    plaintext = "hello 国密 world".encode("utf-8")

    ciphertext = _cbc_encrypt(KEY, FIXED_IV, _pkcs7_pad(plaintext))

    assert ciphertext.hex() == "61e48c1ebc69763fa57932a3713e512791d54fb1fc299da8a8a6054b475eaf5c"


# ---------------------------------------------------------------- 往返


@pytest.mark.parametrize("size", [0, 1, 15, 16, 17, 31, 32, 100, 1000])
def test_encrypt_decrypt_round_trip_by_size(size: int) -> None:
    plaintext = bytes(range(256))[:size] or b""

    sealed = _seal(plaintext)

    assert decrypt(KEY, sealed) == plaintext


def test_encrypt_decrypt_round_trip_chinese_payload() -> None:
    plaintext = '{"票据号码":"12345678","金额":"-100.00"}'.encode("utf-8")

    sealed = _seal(plaintext)

    assert sealed.ciphertext != plaintext
    assert decrypt(KEY, sealed) == plaintext


def test_encrypt_same_plaintext_twice_uses_fresh_iv() -> None:
    first = _seal(b"same plaintext")
    second = _seal(b"same plaintext")

    assert first.iv != second.iv
    assert first.ciphertext != second.ciphertext
    assert first.mac != second.mac
    assert decrypt(KEY, first) == decrypt(KEY, second) == b"same plaintext"


def test_ciphertext_length_is_padded_to_block_boundary() -> None:
    sealed = _seal(b"x" * 17)

    assert len(sealed.ciphertext) == 32
    assert len(sealed.iv) == BLOCK_SIZE
    assert len(sealed.mac) == 32


# ---------------------------------------------------------------- aad


def test_aad_is_bound_into_mac() -> None:
    sealed = _seal(b"payload", aad=b"header-v1")

    assert decrypt(KEY, sealed, aad=b"header-v1") == b"payload"

    with pytest.raises(IntegrityError) as exc:
        decrypt(KEY, sealed, aad=b"header-v2")
    assert exc.value.reason == "mac_mismatch"


def test_trailer_is_bound_into_mac() -> None:
    """信封把收方证书指纹放在密文之后（SCHEMA 的覆盖顺序），必须同样进 MAC。"""
    sealed = encrypt(KEY, b"payload", trailer=b"cert-fp")

    assert decrypt(KEY, sealed, trailer=b"cert-fp") == b"payload"

    with pytest.raises(IntegrityError) as exc:
        decrypt(KEY, sealed, trailer=b"other-fp")
    assert exc.value.reason == "mac_mismatch"


def test_cbc_matches_gmssl_reference_implementation() -> None:
    """与 S4-3 用过的 gmssl one_round + 手写 CBC 对照，证明换 OpenSSL 后端后口径不变。"""
    from gmssl.sm4 import SM4_ENCRYPT, CryptSM4

    def gmssl_cbc(key: bytes, iv: bytes, data: bytes) -> bytes:
        cipher = CryptSM4()
        cipher.set_key(key, SM4_ENCRYPT)
        previous, out = iv, b""
        for offset in range(0, len(data), 16):
            block = bytes(a ^ b for a, b in zip(data[offset : offset + 16], previous))
            previous = bytes(cipher.one_round(cipher.sk, list(block)))
            out += previous
        return out

    plaintext = "hello 国密 world".encode("utf-8")
    padded = _pkcs7_pad(plaintext)

    assert _cbc_encrypt(KEY, FIXED_IV, padded) == gmssl_cbc(KEY, FIXED_IV, padded)


# ---------------------------------------------------------------- 篡改（Encrypt-then-MAC）


def test_tampered_ciphertext_fails_mac() -> None:
    sealed = _seal(b"integrity matters")
    tampered = bytearray(sealed.ciphertext)
    tampered[3] ^= 0x01

    with pytest.raises(IntegrityError) as exc:
        decrypt(KEY, Sm4Ciphertext(iv=sealed.iv, ciphertext=bytes(tampered), mac=sealed.mac))
    assert exc.value.reason == "mac_mismatch"


def test_tampered_iv_fails_mac() -> None:
    sealed = _seal(b"integrity matters")
    tampered_iv = bytes([sealed.iv[0] ^ 0xFF]) + sealed.iv[1:]

    with pytest.raises(IntegrityError) as exc:
        decrypt(KEY, Sm4Ciphertext(iv=tampered_iv, ciphertext=sealed.ciphertext, mac=sealed.mac))
    assert exc.value.reason == "mac_mismatch"


def test_tampered_mac_fails_mac() -> None:
    sealed = _seal(b"integrity matters")
    tampered_mac = bytes([sealed.mac[0] ^ 0xFF]) + sealed.mac[1:]

    with pytest.raises(IntegrityError) as exc:
        decrypt(KEY, Sm4Ciphertext(iv=sealed.iv, ciphertext=sealed.ciphertext, mac=tampered_mac))
    assert exc.value.reason == "mac_mismatch"


def test_wrong_key_fails_mac() -> None:
    sealed = _seal(b"integrity matters")

    with pytest.raises(IntegrityError) as exc:
        decrypt(b"fedcba9876543210", sealed)
    assert exc.value.reason == "mac_mismatch"


def test_truncated_ciphertext_fails() -> None:
    sealed = _seal(b"x" * 48)

    with pytest.raises(IntegrityError):
        decrypt(KEY, Sm4Ciphertext(iv=sealed.iv, ciphertext=sealed.ciphertext[:-16], mac=sealed.mac))


# ---------------------------------------------------------------- 填充校验


@pytest.mark.parametrize("bad_last_byte", [0x00, 0x05, 0x11], ids=["zero", "mismatch", "too_large"])
def test_invalid_padding_raises_bad_padding(bad_last_byte: int) -> None:
    """构造 MAC 合法但填充非法的密文：必须证明"去填充"是独立的一道闸。"""
    crafted_block = b"x" * 15 + bytes([bad_last_byte])
    iv = FIXED_IV
    ciphertext = _cbc_encrypt(KEY, iv, crafted_block)
    mac = hmac_sm3(derive_mac_key(KEY), iv + ciphertext)

    with pytest.raises(IntegrityError) as exc:
        decrypt(KEY, Sm4Ciphertext(iv=iv, ciphertext=ciphertext, mac=mac))
    assert exc.value.reason == "bad_padding"


def test_bad_padding_with_wrong_mac_reports_mac_first() -> None:
    """解密前必须先验 MAC：坏填充 + 坏 MAC 只能报 mac_mismatch。"""
    crafted_block = b"x" * 15 + b"\x05"
    ciphertext = _cbc_encrypt(KEY, FIXED_IV, crafted_block)

    with pytest.raises(IntegrityError) as exc:
        decrypt(KEY, Sm4Ciphertext(iv=FIXED_IV, ciphertext=ciphertext, mac=b"\x00" * 32))
    assert exc.value.reason == "mac_mismatch"


# ---------------------------------------------------------------- 结构错误


@pytest.mark.parametrize("key", [b"", b"short", b"k" * 15, b"k" * 17])
def test_invalid_key_length_raises_value_error(key: bytes) -> None:
    with pytest.raises(ValueError):
        encrypt(key, b"data")

    with pytest.raises(ValueError):
        decrypt(key, Sm4Ciphertext(iv=FIXED_IV, ciphertext=b"x" * 16, mac=b"m" * 32))


def test_invalid_iv_or_mac_length_raises_value_error() -> None:
    sealed = _seal(b"data")

    with pytest.raises(ValueError):
        decrypt(KEY, Sm4Ciphertext(iv=b"short", ciphertext=sealed.ciphertext, mac=sealed.mac))

    with pytest.raises(ValueError):
        decrypt(KEY, Sm4Ciphertext(iv=sealed.iv, ciphertext=sealed.ciphertext, mac=b"short"))


def test_integrity_error_does_not_leak_plaintext() -> None:
    secret = "super-secret-ticket-amount-123456"
    sealed = _seal(secret.encode("utf-8"))
    tampered = bytes([sealed.ciphertext[0] ^ 0xFF]) + sealed.ciphertext[1:]

    with pytest.raises(IntegrityError) as exc:
        decrypt(KEY, Sm4Ciphertext(iv=sealed.iv, ciphertext=tampered, mac=sealed.mac))
    assert secret not in str(exc.value)


def test_ciphertext_record_is_frozen() -> None:
    sealed = _seal(b"data")

    with pytest.raises(Exception):
        sealed.iv = b"\x00" * 16  # type: ignore[misc]


def test_derive_mac_key_is_separate_from_encryption_key() -> None:
    mac_key = derive_mac_key(KEY)

    assert len(mac_key) == 32
    assert mac_key != KEY