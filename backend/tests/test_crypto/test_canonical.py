"""S4-1：canonical JSON 规范化的字节级契约。

每条用例对应 `docs/spec/s4-1-canonical-sm3.md` §3 的一条规则；失败信息里带实际字节，
方便一眼看出是"排序错了"还是"转义错了"。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest

from app.crypto.canonical import (
    CanonicalJSONError,
    canonical_json,
    canonical_sha256,
)


# ---------------------------------------------------------------- 键序与分隔符


def test_canonical_json_key_order_different_same_bytes_and_digest() -> None:
    left = {"b": 1, "a": 2}
    right = {"a": 2, "b": 1}

    assert canonical_json(left) == canonical_json(right) == b'{"a":2,"b":1}'
    assert canonical_sha256(left) == canonical_sha256(right)


def test_canonical_json_separators_have_no_whitespace() -> None:
    out = canonical_json({"b": [1, 2], "a": {"d": 3, "c": 4}})

    assert out == b'{"a":{"c":4,"d":3},"b":[1,2]}'
    assert b" " not in out
    assert b"\n" not in out


def test_canonical_json_sorts_keys_by_unicode_code_point() -> None:
    # 码点：Z=90 < a=97 < 中=20013 < 😀=128512；
    # JS 默认按 UTF-16 码元排序会得到 Z,a,😀,中，必须显式按码点比（见 spec §3 的跨语言坑）。
    out = canonical_json({"😀": 4, "中": 3, "a": 2, "Z": 1})

    assert out == '{"Z":1,"a":2,"中":3,"😀":4}'.encode("utf-8")


# ---------------------------------------------------------------- 值类型


def test_canonical_json_chinese_value_is_not_ascii_escaped() -> None:
    out = canonical_json({"name": "发票"})

    assert out == '{"name":"发票"}'.encode("utf-8")
    assert b"\\u" not in out


def test_canonical_json_negative_number_and_nested_list() -> None:
    out = canonical_json({"amount": "-100.00", "items": [1, -2, [3, None]]})

    assert out == b'{"amount":"-100.00","items":[1,-2,[3,null]]}'


def test_canonical_json_none_stays_null() -> None:
    assert canonical_json({"a": None}) == b'{"a":null}'


def test_canonical_json_empty_containers() -> None:
    assert canonical_json({}) == b"{}"
    assert canonical_json([]) == b"[]"
    assert canonical_json({"a": {}, "b": []}) == b'{"a":{},"b":[]}'


def test_canonical_json_control_characters_use_json_escapes() -> None:
    out = canonical_json({"a": "x\ny\t\x01"})

    assert out == b'{"a":"x\\ny\\t\\u0001"}'
    # 与标准库的转义一致（只差分隔符空格，这里也压成紧凑格式）
    assert out.decode("utf-8") == json.dumps({"a": "x\ny\t\x01"}, separators=(",", ":"))


def test_canonical_json_bool_is_not_confused_with_int() -> None:
    assert canonical_json({"a": True}) == b'{"a":true}'
    assert canonical_json({"a": False}) == b'{"a":false}'
    assert canonical_json({"a": 1}) == b'{"a":1}'
    assert canonical_sha256({"a": True}) != canonical_sha256({"a": 1})


# ---------------------------------------------------------------- 金额：100 vs 100.00


def test_canonical_json_int_and_string_amount_produce_different_digests() -> None:
    as_int = canonical_json({"amount": 100})
    as_string = canonical_json({"amount": "100.00"})
    as_plain_string = canonical_json({"amount": "100"})

    assert as_int == b'{"amount":100}'
    assert as_string == b'{"amount":"100.00"}'
    assert as_plain_string == b'{"amount":"100"}'
    digests = {canonical_sha256({"amount": v}) for v in (100, "100.00", "100")}
    assert len(digests) == 3


# ---------------------------------------------------------------- 拒绝：float / Decimal


@pytest.mark.parametrize(
    "bad",
    [1.5, 100.0, float("nan"), float("inf"), float("-inf")],
    ids=["float", "float_integral", "nan", "inf", "neg_inf"],
)
def test_canonical_json_rejects_float(bad: float) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json({"amount": bad})


@pytest.mark.parametrize("bad", [Decimal("1"), Decimal("100.00")])
def test_canonical_json_rejects_decimal(bad: Decimal) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json({"amount": bad})


# ---------------------------------------------------------------- 拒绝：键与整数范围


@pytest.mark.parametrize("key", [1, None, (1, 2), True], ids=["int", "none", "tuple", "bool"])
def test_canonical_json_rejects_non_str_key(key: object) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json({key: "v"})


def test_canonical_json_rejects_int_outside_safe_range() -> None:
    assert canonical_json({"n": 2**53 - 1}) == b'{"n":9007199254740991}'
    assert canonical_json({"n": -(2**53 - 1)}) == b'{"n":-9007199254740991}'

    for bad in (2**53, -(2**53), 2**63):
        with pytest.raises(CanonicalJSONError):
            canonical_json({"n": bad})


# ---------------------------------------------------------------- 拒绝：非 JSON 对象


@pytest.mark.parametrize(
    "bad",
    [b"bytes", bytearray(b"x"), (1, 2), {1, 2}, frozenset({1}), object()],
    ids=["bytes", "bytearray", "tuple", "set", "frozenset", "object"],
)
def test_canonical_json_rejects_unsupported_objects(bad: object) -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json({"v": bad})


def test_canonical_json_rejects_tuple_at_root() -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json((1, 2))


# ---------------------------------------------------------------- 拒绝：循环引用 / 非法码元


def test_canonical_json_rejects_circular_reference() -> None:
    cycle_list: list[object] = []
    cycle_list.append(cycle_list)
    with pytest.raises(CanonicalJSONError):
        canonical_json(cycle_list)

    cycle_dict: dict[str, object] = {}
    cycle_dict["self"] = cycle_dict
    with pytest.raises(CanonicalJSONError):
        canonical_json(cycle_dict)


def test_canonical_json_rejects_lone_surrogate() -> None:
    with pytest.raises(CanonicalJSONError):
        canonical_json({"a": "\ud800"})


# ---------------------------------------------------------------- 不变式


def test_canonical_json_is_deterministic_and_does_not_mutate_input() -> None:
    payload = {"b": [1, {"d": None, "c": "中文"}], "a": True, "n": -7}
    snapshot = json.loads(json.dumps(payload))
    first = canonical_json(payload)

    for _ in range(100):
        assert canonical_json(payload) == first
    assert payload == snapshot


def test_canonical_json_round_trips_through_utf8_json() -> None:
    payload = {"发票号码": "12345678", "items": [{"amount": "-1.00"}], "ok": None}
    encoded = canonical_json(payload)

    assert isinstance(encoded, bytes)
    assert encoded.decode("utf-8").encode("utf-8") == encoded
    assert json.loads(encoded.decode("utf-8")) == payload
    assert not encoded.startswith(b"\xef\xbb\xbf")  # 无 BOM
    assert not encoded.endswith(b"\n")


def test_canonical_sha256_matches_hashlib_and_is_lowercase_hex() -> None:
    payload = {"a": 1, "b": ["x", None], "c": "发票"}
    digest = canonical_sha256(payload)

    assert digest == hashlib.sha256(canonical_json(payload)).hexdigest()
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)  # 合法十六进制