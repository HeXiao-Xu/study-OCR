"""S4-2：SM2 签名/验签（SM2-SM3，含国标 ZA 绑定）。

策略写死：只走 `sign_with_sm3` / `verify_with_sm3`（e = SM3(ZA‖M)），
**绝不把外部摘要当 e 直接签**（双重哈希陷阱，见 `test_verify_bytes_double_hash_trap`）。

标准向量：GB/T 32918.5-2017 附录 A.1（推荐曲线 sm2p256v1）。
"""
from __future__ import annotations

import pytest
from Cryptodome.Util.asn1 import DerSequence

from app.crypto.sm.sm2 import (
    DEFAULT_USER_ID,
    SM2_N,
    decrypt_bytes,
    encrypt_bytes,
    sign_bytes,
    verify_bytes,
)
from tests.test_crypto._keys import (
    EDGE_PRIVATE,
    EDGE_PUBLIC,
    VECTOR_K,
    VECTOR_MESSAGE,
    VECTOR_PRIVATE,
    VECTOR_PUBLIC,
    VECTOR_R,
    VECTOR_S,
)


def _decode(signature_hex: str) -> tuple[int, int]:
    seq = DerSequence()
    seq.decode(bytes.fromhex(signature_hex))
    return int(seq[0]), int(seq[1])


# ---------------------------------------------------------------- 国标向量


def test_sign_bytes_gb_t_vector_matches_standard() -> None:
    signature = sign_bytes(
        VECTOR_MESSAGE,
        private_key_hex=VECTOR_PRIVATE,
        public_key_hex=VECTOR_PUBLIC,
        k_hex=VECTOR_K,
    )

    assert _decode(signature) == (VECTOR_R, VECTOR_S)
    assert verify_bytes(VECTOR_MESSAGE, signature, public_key_hex=VECTOR_PUBLIC) is True


def test_verify_bytes_accepts_vector_signature_on_any_key_spelling() -> None:
    signature = sign_bytes(
        VECTOR_MESSAGE,
        private_key_hex=VECTOR_PRIVATE,
        public_key_hex=VECTOR_PUBLIC,
        k_hex=VECTOR_K,
    )

    assert verify_bytes(VECTOR_MESSAGE, signature, public_key_hex=VECTOR_PUBLIC) is True
    # 130 hex（带未压缩点前缀 04）与大小写混合都必须等价
    assert verify_bytes(VECTOR_MESSAGE, signature, public_key_hex="04" + VECTOR_PUBLIC) is True
    assert verify_bytes(VECTOR_MESSAGE, signature, public_key_hex=VECTOR_PUBLIC.lower()) is True


def test_default_user_id_is_national_default() -> None:
    assert DEFAULT_USER_ID == b"1234567812345678"


# ---------------------------------------------------------------- 往返与篡改


def test_sign_bytes_round_trip_ok() -> None:
    signature = sign_bytes(b"canonical-payload", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)

    assert verify_bytes(b"canonical-payload", signature, public_key_hex=VECTOR_PUBLIC) is True


def test_verify_bytes_rejects_one_bit_message_change() -> None:
    signature = sign_bytes(b"abc", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)

    assert verify_bytes(b"abd", signature, public_key_hex=VECTOR_PUBLIC) is False


def test_verify_bytes_rejects_tampered_signature() -> None:
    signature = sign_bytes(b"abc", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)
    replacement = "0" if signature[10] != "0" else "1"
    tampered = signature[:10] + replacement + signature[11:]

    assert verify_bytes(b"abc", tampered, public_key_hex=VECTOR_PUBLIC) is False


def test_verify_bytes_rejects_wrong_public_key() -> None:
    signature = sign_bytes(b"abc", private_key_hex=EDGE_PRIVATE, public_key_hex=EDGE_PUBLIC)

    assert verify_bytes(b"abc", signature, public_key_hex=VECTOR_PUBLIC) is False


# ---------------------------------------------------------------- 随机源


def test_sign_bytes_random_k_produces_different_signatures() -> None:
    first = sign_bytes(b"same message", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)
    second = sign_bytes(b"same message", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)

    assert first != second


def test_sign_bytes_fixed_k_is_deterministic() -> None:
    first = sign_bytes(b"same message", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC, k_hex=VECTOR_K)
    second = sign_bytes(b"same message", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC, k_hex=VECTOR_K)

    assert first == second


# ---------------------------------------------------------------- 双重哈希陷阱


def test_verify_bytes_double_hash_trap() -> None:
    """把 SM3 摘要当"消息"再签 = 双重哈希：对原消息必须验不过。

    这正是本项目**只有 sign_with_sm3 一个入口**的原因；S5 只能传 canonical 字节，
    不能传 digest。
    """
    from app.crypto.sm.sm3 import sm3_digest

    payload = b"canonical-payload"
    digest = sm3_digest(payload)
    wrongly_signed = sign_bytes(digest, private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)

    assert verify_bytes(digest, wrongly_signed, public_key_hex=VECTOR_PUBLIC) is True  # 对摘要本身合法
    assert verify_bytes(payload, wrongly_signed, public_key_hex=VECTOR_PUBLIC) is False  # 对原消息必然失败


# ---------------------------------------------------------------- gmssl 缺陷回归


def test_sign_bytes_public_key_x_starting_with_04() -> None:
    """gmssl 的 lstrip("04") 会剥掉 x 开头的 04；封装层必须覆盖回正确公钥。"""
    signature = sign_bytes(b"edge-key", private_key_hex=EDGE_PRIVATE, public_key_hex=EDGE_PUBLIC)

    assert verify_bytes(b"edge-key", signature, public_key_hex=EDGE_PUBLIC) is True
    assert verify_bytes(b"edge-key", signature, public_key_hex="04" + EDGE_PUBLIC) is True


def test_verify_bytes_rejects_s_plus_n_malleability() -> None:
    """gmssl 不校验 s ∈ [1,n-1]；s+n 的变体在它眼里也合法，封装层必须拒绝。"""
    from Cryptodome.Util.asn1 import DerInteger

    signature = sign_bytes(b"abc", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)
    r, s = _decode(signature)
    malleable_seq = DerSequence([DerInteger(r), DerInteger(s + SM2_N)])

    assert verify_bytes(b"abc", signature, public_key_hex=VECTOR_PUBLIC) is True
    assert verify_bytes(b"abc", malleable_seq.encode().hex(), public_key_hex=VECTOR_PUBLIC) is False


# ---------------------------------------------------------------- 非法输入


@pytest.mark.parametrize(
    "signature",
    ["", "zz", "0", "00", "3045022100", "3044022000" + "00" * 30 + "0201"],
    ids=["empty", "non_hex", "one_char", "one_byte", "truncated", "malformed_der"],
)
def test_verify_bytes_returns_false_for_malformed_signature(signature: str) -> None:
    assert verify_bytes(b"abc", signature, public_key_hex=VECTOR_PUBLIC) is False


@pytest.mark.parametrize(
    "private_key",
    [
        "",
        "00",
        "0" * 64,
        "zz" * 32,
        f"{SM2_N:064x}",
        f"{SM2_N - 1:064x}",
    ],
    ids=["empty", "short", "zero", "non_hex", "equals_n", "n_minus_1"],
)
def test_sign_bytes_rejects_invalid_private_key(private_key: str) -> None:
    with pytest.raises(ValueError):
        sign_bytes(b"abc", private_key_hex=private_key, public_key_hex=VECTOR_PUBLIC)


@pytest.mark.parametrize(
    "public_key",
    [
        "",
        "00",
        "zz" * 64,
        "00" * 64,  # (0,0) 不在曲线上
    ],
    ids=["empty", "short", "non_hex", "not_on_curve"],
)
def test_verify_bytes_raises_for_structurally_invalid_public_key(public_key: str) -> None:
    with pytest.raises(ValueError):
        verify_bytes(b"abc", "3045022100", public_key_hex=public_key)


def test_sign_bytes_rejects_non_bytes_message() -> None:
    with pytest.raises(TypeError):
        sign_bytes("abc", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)  # type: ignore[arg-type]


@pytest.mark.parametrize("k_hex", ["", "0" * 64, "zz" * 32, f"{SM2_N:064x}"])
def test_sign_bytes_rejects_invalid_fixed_k(k_hex: str) -> None:
    with pytest.raises(ValueError):
        sign_bytes(b"abc", private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC, k_hex=k_hex)


# ---------------------------------------------------------------- 边界消息


def test_sign_bytes_round_trips_empty_and_large_messages() -> None:
    for message in (b"", b"x", b"y" * 65536):
        signature = sign_bytes(message, private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC)
        assert verify_bytes(message, signature, public_key_hex=VECTOR_PUBLIC) is True


def test_sign_bytes_accepts_bytearray_and_memoryview() -> None:
    expected = sign_bytes(
        b"abc",
        private_key_hex=VECTOR_PRIVATE,
        public_key_hex=VECTOR_PUBLIC,
        k_hex=VECTOR_K,
    )

    assert (
        sign_bytes(bytearray(b"abc"), private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC, k_hex=VECTOR_K)
        == expected
    )
    assert (
        sign_bytes(memoryview(b"abc"), private_key_hex=VECTOR_PRIVATE, public_key_hex=VECTOR_PUBLIC, k_hex=VECTOR_K)
        == expected
    )


# ---------------------------------------------------------------- SM2 加解密（S4-4）


@pytest.mark.parametrize("size", [1, 16, 32])
def test_sm2_encrypt_decrypt_round_trip(size: int) -> None:
    cek = bytes(range(size))

    ciphertext = encrypt_bytes(cek, public_key_hex=VECTOR_PUBLIC)

    assert len(ciphertext) == 64 + 32 + size  # C1 ‖ C3 ‖ C2，C1 不带 04 前缀
    assert decrypt_bytes(ciphertext, private_key_hex=VECTOR_PRIVATE) == cek


def test_sm2_encrypt_rejects_plaintext_outside_1_to_32_bytes() -> None:
    for bad in (b"", b"x" * 33, b"x" * 100):
        with pytest.raises(ValueError):
            encrypt_bytes(bad, public_key_hex=VECTOR_PUBLIC)


def test_sm2_encrypt_uses_fresh_randomness() -> None:
    first = encrypt_bytes(b"\x00" * 16, public_key_hex=VECTOR_PUBLIC)
    second = encrypt_bytes(b"\x00" * 16, public_key_hex=VECTOR_PUBLIC)

    assert first != second
    assert decrypt_bytes(first, private_key_hex=VECTOR_PRIVATE) == decrypt_bytes(second, private_key_hex=VECTOR_PRIVATE)


def test_sm2_decrypt_rejects_bad_length() -> None:
    for bad in (b"", b"x" * 96, b"x" * 129):
        with pytest.raises(ValueError):
            decrypt_bytes(bad, private_key_hex=VECTOR_PRIVATE)


def test_sm2_decrypt_rejects_c1_not_on_curve() -> None:
    # C1 = (0,0) 不在 sm2p256v1 上
    with pytest.raises(ValueError):
        decrypt_bytes(b"\x00" * 64 + b"\x00" * 32 + b"\x01" * 16, private_key_hex=VECTOR_PRIVATE)


def test_sm2_decrypt_detects_tampered_c1_or_c2() -> None:
    plaintext = b"0123456789abcdef"
    ciphertext = bytearray(encrypt_bytes(plaintext, public_key_hex=VECTOR_PUBLIC))

    tampered_c2 = bytearray(ciphertext)
    tampered_c2[-1] ^= 0x01
    assert decrypt_bytes(bytes(tampered_c2), private_key_hex=VECTOR_PRIVATE) != plaintext

    tampered_c1 = bytearray(ciphertext)
    tampered_c1[0] ^= 0xFF
    try:
        result = decrypt_bytes(bytes(tampered_c1), private_key_hex=VECTOR_PRIVATE)
    except ValueError:
        result = None  # C1 落到曲线外也是合法拒绝
    assert result != plaintext


def test_sm2_encrypt_interops_with_gmssl_decrypt() -> None:
    """我们加密 → gmssl（C1C3C2）能解开，证明格式与国标排列一致。"""
    from gmssl.sm2 import CryptSM2

    plaintext = b"fedcba9876543210"
    ciphertext = encrypt_bytes(plaintext, public_key_hex=VECTOR_PUBLIC)

    gmssl_cipher = CryptSM2(private_key=VECTOR_PRIVATE, public_key=VECTOR_PUBLIC, mode=1)
    assert gmssl_cipher.decrypt(ciphertext) == plaintext


def test_sm2_decrypt_interops_with_gmssl_encrypt() -> None:
    """gmssl 加密 → 我们能解开（接受标准 C1C3C2 布局）。"""
    from gmssl.sm2 import CryptSM2

    plaintext = b"0123456789abcdef"
    gmssl_cipher = CryptSM2(private_key="", public_key=VECTOR_PUBLIC, mode=1)
    ciphertext = gmssl_cipher.encrypt(plaintext)

    assert decrypt_bytes(ciphertext, private_key_hex=VECTOR_PRIVATE) == plaintext