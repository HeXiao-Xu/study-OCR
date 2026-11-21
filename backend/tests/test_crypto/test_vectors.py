"""S4-8：密码模块集中测试向量（每条注明出处，论文可直接引用）。

原则：**不冒充国标**——有国标向量的写标准号 + 附录；HMAC-SM3 没有国标向量，
用 OpenSSL 3.5 交叉计算的向量，并明确标注（D-076）。
"""
from __future__ import annotations

import binascii

import pytest
from Cryptodome.Util.asn1 import DerSequence

from app.crypto.canonical import canonical_json
from app.crypto.sm.sm2 import sign_bytes
from app.crypto.sm.sm3 import hmac_sm3, sm3_digest
from app.crypto.sm.sm4 import _encrypt_block
from tests.test_crypto._keys import VECTOR_K, VECTOR_MESSAGE, VECTOR_PRIVATE, VECTOR_PUBLIC, VECTOR_R, VECTOR_S

# ---------------------------------------------------------------- 向量表


# GB/T 32905-2016《SM3 密码杂凑算法》附录 A 示例
SM3_VECTORS = [
    (b"abc", "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0", "附录 A.1"),
    (
        b"abcd" * 16,
        "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732",
        "附录 A.2（512 bit 消息）",
    ),
    (b"", "1ab21d8355cfa17f8e61194831e81a8f22bec8c728fefb747ed035eb5082aa2b", "空消息（常用边界向量）"),
]

# GB/T 32907-2016《SM4 分组密码算法》附录 A.1：单块 ECB 示例
SM4_KEY = bytes.fromhex("0123456789abcdeffedcba9876543210")
SM4_PLAINTEXT = bytes.fromhex("0123456789abcdeffedcba9876543210")
SM4_CIPHERTEXT = "681edf34d206965e86b3e94f536e4246"

# HMAC-SM3：HMAC 不是 GB/T 定义内容，没有国标向量；以下由 OpenSSL 3.5（Node createHmac('sm3')）计算
HMAC_SM3_VECTORS = [
    (b"key", b"The quick brown fox jumps over the lazy dog", "bd4a34077888162b210645b8ebf74b9af357303789357a27c7fc457244ebd398"),
    (b"k" * 100, b"data", "d2e3191973421dc0792b6ec5e1a5b5c26652d764b024ba8c20221f0396fedfe8"),
    (b"", b"", "0d23f72ba15e9c189a879aefc70996b06091de6e64d31b7a84004356dd915261"),
    (b"secret", b"canonical-payload", "34be46034c9ae95ecb3022723c6143be9bb264ef2e76ba81381960dee8dad064"),
]

# 本项目 canonical JSON 口径（D-074），与 Node 侧按码点排序实现逐字对照
CANONICAL_VECTORS = [
    ({"b": 1, "a": 2}, b'{"a":2,"b":1}'),
    ({"name": "发票"}, '{"name":"发票"}'.encode("utf-8")),
    ({"a": "x\ny\t\x01"}, b'{"a":"x\\ny\\t\\u0001"}'),
]


# ---------------------------------------------------------------- SM3


@pytest.mark.parametrize(("message", "expected", "source"), SM3_VECTORS, ids=["abc", "512bit", "empty"])
def test_sm3_gb_t_32905_vectors(message: bytes, expected: str, source: str) -> None:
    assert sm3_digest(message).hex() == expected, f"GB/T 32905-2016 {source}"


# ---------------------------------------------------------------- SM4


def test_sm4_gb_t_32907_single_block_vector() -> None:
    # GB/T 32907-2016 附录 A.1：单块 ECB
    assert _encrypt_block(SM4_KEY, SM4_PLAINTEXT).hex() == SM4_CIPHERTEXT


# ---------------------------------------------------------------- SM2


def test_sm2_gb_t_32918_5_signature_vector() -> None:
    """GB/T 32918.5-2017 附录 A.1：固定 k 下签名逐位复现（推荐曲线 sm2p256v1）。"""
    signature = sign_bytes(
        VECTOR_MESSAGE,
        private_key_hex=VECTOR_PRIVATE,
        public_key_hex=VECTOR_PUBLIC,
        k_hex=VECTOR_K,
    )
    sequence = DerSequence()
    sequence.decode(bytes.fromhex(signature))

    assert (sequence[0], sequence[1]) == (VECTOR_R, VECTOR_S)


# ---------------------------------------------------------------- HMAC-SM3


@pytest.mark.parametrize(
    ("key", "data", "expected"),
    HMAC_SM3_VECTORS,
    ids=["openssl_vector", "long_key", "empty", "business"],
)
def test_hmac_sm3_openssl_cross_vectors(key: bytes, data: bytes, expected: str) -> None:
    # 出处：OpenSSL 3.5 交叉计算（非国标向量，D-076）
    assert hmac_sm3(key, data).hex() == expected


# ---------------------------------------------------------------- canonical JSON


@pytest.mark.parametrize(("payload", "expected"), CANONICAL_VECTORS, ids=["key_order", "chinese", "control_chars"])
def test_canonical_json_byte_vectors(payload: dict, expected: bytes) -> None:
    assert canonical_json(payload) == expected