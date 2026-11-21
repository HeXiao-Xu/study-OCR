"""S4-1：SM3 唯一入口的国标向量与入参契约。

向量出处：GB/T 32905-2016（SM3 密码杂凑算法）示例。
"""
from __future__ import annotations

import pytest

from app.crypto.sm.sm3 import hmac_sm3, sm3_digest, sm3_hex

# GB/T 32905-2016 附录 A 示例
VECTOR_ABC = "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
VECTOR_512BIT = "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732"
VECTOR_EMPTY = "1ab21d8355cfa17f8e61194831e81a8f22bec8c728fefb747ed035eb5082aa2b"
SIXTY_FOUR_ABCD = b"abcd" * 16


# ---------------------------------------------------------------- 国标向量


def test_sm3_digest_gb_t_vector_abc() -> None:
    assert sm3_digest(b"abc").hex() == VECTOR_ABC


def test_sm3_digest_gb_t_vector_512_bit_message() -> None:
    assert sm3_digest(SIXTY_FOUR_ABCD).hex() == VECTOR_512BIT


def test_sm3_digest_empty_input() -> None:
    assert sm3_digest(b"").hex() == VECTOR_EMPTY


# ---------------------------------------------------------------- 返回形态


def test_sm3_digest_returns_32_bytes() -> None:
    digest = sm3_digest(b"abc")

    assert isinstance(digest, bytes)
    assert len(digest) == 32


def test_sm3_hex_matches_digest_and_is_lowercase_hex() -> None:
    hex_digest = sm3_hex(b"abc")

    assert hex_digest == sm3_digest(b"abc").hex() == VECTOR_ABC
    assert len(hex_digest) == 64
    assert hex_digest == hex_digest.lower()
    int(hex_digest, 16)


# ---------------------------------------------------------------- 篡改与确定性


def test_sm3_digest_one_bit_change_changes_digest() -> None:
    assert sm3_digest(b"abc") != sm3_digest(b"abd")
    # 只改 1 bit（0x61 'a' -> 0x60 '`'）
    assert sm3_digest(b"abc") != sm3_digest(b"`bc")


def test_sm3_digest_is_deterministic() -> None:
    payload = b"\x00\x01\x02\xff" * 100

    assert len({sm3_digest(payload).hex() for _ in range(20)}) == 1


# ---------------------------------------------------------------- 入参契约


def test_sm3_digest_does_not_mutate_bytearray_input() -> None:
    buffer = bytearray(b"abc")
    before = bytes(buffer)

    sm3_digest(buffer)

    assert bytes(buffer) == before  # gmssl 的 sm3_hash 会原地改入参，封装层必须挡住


def test_sm3_digest_accepts_bytearray_and_memoryview() -> None:
    expected = sm3_digest(b"abc")

    assert sm3_digest(bytearray(b"abc")) == expected
    assert sm3_digest(memoryview(b"abc")) == expected


def test_sm3_digest_rejects_str_input() -> None:
    with pytest.raises(TypeError):
        sm3_digest("abc")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        sm3_hex("abc")  # type: ignore[arg-type]


# ---------------------------------------------------------------- 填充边界


@pytest.mark.parametrize("size", [1, 55, 56, 63, 64, 65, 119, 120, 1000])
def test_sm3_digest_padding_boundaries(size: int) -> None:
    digest = sm3_digest(b"a" * size)

    assert len(digest) == 32


def test_sm3_digest_different_sizes_have_different_digests() -> None:
    sizes = [55, 56, 63, 64, 65]

    assert len({sm3_digest(b"a" * n).hex() for n in sizes}) == len(sizes)


# ---------------------------------------------------------------- HMAC-SM3（S4-3）
# HMAC 不是国标定义的算法，没有"国标向量"；以下期望值由 OpenSSL 3（Node v25
# `createHmac('sm3', key)`）独立计算，用于跨实现对照（D-076）。

HMAC_VECTORS = [
    (
        b"key",
        b"The quick brown fox jumps over the lazy dog",
        "bd4a34077888162b210645b8ebf74b9af357303789357a27c7fc457244ebd398",
    ),
    (b"k" * 100, b"data", "d2e3191973421dc0792b6ec5e1a5b5c26652d764b024ba8c20221f0396fedfe8"),
    (b"", b"", "0d23f72ba15e9c189a879aefc70996b06091de6e64d31b7a84004356dd915261"),
    (b"secret", b"canonical-payload", "34be46034c9ae95ecb3022723c6143be9bb264ef2e76ba81381960dee8dad064"),
]


@pytest.mark.parametrize(
    ("key", "data", "expected"),
    HMAC_VECTORS,
    ids=["openssl_vector", "long_key", "empty_key_and_data", "business_shape"],
)
def test_hmac_sm3_matches_openssl_cross_vector(key: bytes, data: bytes, expected: str) -> None:
    assert hmac_sm3(key, data).hex() == expected


def test_hmac_sm3_returns_32_bytes() -> None:
    mac = hmac_sm3(b"key", b"data")

    assert isinstance(mac, bytes)
    assert len(mac) == 32


def test_hmac_sm3_is_not_plain_concat_hash() -> None:
    key, data = b"secret", b"canonical-payload"

    assert hmac_sm3(key, data) != sm3_digest(key + data)


def test_hmac_sm3_hashes_overlong_key_first() -> None:
    # RFC 2104：key 长于分组长时，先哈希；因此两条调用必须等价。
    long_key = b"k" * 100

    assert hmac_sm3(long_key, b"data") == hmac_sm3(sm3_digest(long_key), b"data")


def test_hmac_sm3_accepts_bytearray_and_memoryview() -> None:
    expected = hmac_sm3(b"key", b"data")

    assert hmac_sm3(bytearray(b"key"), memoryview(b"data")) == expected


def test_hmac_sm3_rejects_str_inputs() -> None:
    with pytest.raises(TypeError):
        hmac_sm3("key", b"data")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        hmac_sm3(b"key", "data")  # type: ignore[arg-type]


# ---------------------------------------------------------------- 双后端（S4-4）


def _use_gmssl_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.crypto.sm import sm3 as sm3_module

    monkeypatch.setattr(sm3_module, "_OPENSSL_SM3", False)


def test_sm3_digest_gmssl_fallback_matches_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """平台没有 OpenSSL SM3 时必须回退 gmssl，且结果一字不差。"""
    _use_gmssl_fallback(monkeypatch)

    assert sm3_digest(b"abc").hex() == VECTOR_ABC
    assert sm3_digest(SIXTY_FOUR_ABCD).hex() == VECTOR_512BIT
    assert sm3_digest(b"").hex() == VECTOR_EMPTY


def test_hmac_sm3_gmssl_fallback_matches_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_gmssl_fallback(monkeypatch)

    for key, data, expected in HMAC_VECTORS:
        assert hmac_sm3(key, data).hex() == expected


def test_sm3_backends_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    """同输入两条路径逐字节一致（覆盖 55/56/64 填充边界）。"""
    payloads = [b"", b"abc", b"a" * 55, b"a" * 56, b"a" * 64, b"a" * 1000]
    fast = [sm3_digest(item) for item in payloads]

    _use_gmssl_fallback(monkeypatch)
    slow = [sm3_digest(item) for item in payloads]

    assert fast == slow