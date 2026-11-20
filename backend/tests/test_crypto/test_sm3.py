"""S4-1：SM3 唯一入口的国标向量与入参契约。

向量出处：GB/T 32905-2016（SM3 密码杂凑算法）示例。
"""
from __future__ import annotations

import pytest

from app.crypto.sm.sm3 import sm3_digest, sm3_hex

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