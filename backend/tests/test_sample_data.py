"""S1-1 字段值生成的单元测试。

这些函数决定了"合成数据的标注是否正确"：金额大小写、统一社会信用代码校验位一旦算错，
S1-5 的校验规则就会把正确实现判成错误——所以必须单独钉死。
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from sample_data import (
    amount_to_chinese_upper,
    gen_id_card,
    gen_invoice_code,
    gen_invoice_no,
    gen_uscc,
    is_valid_uscc,
    money_text,
    uscc_check_digit,
)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0", "零元整"),
        ("0.05", "零元零伍分"),
        ("100.10", "壹佰元壹角整"),
        ("1000", "壹仟元整"),
        ("1234.56", "壹仟贰佰叁拾肆元伍角陆分"),
        ("10000", "壹万元整"),
        ("100000", "壹拾万元整"),
        ("11230.05", "壹万壹仟贰佰叁拾元零伍分"),
        ("200000.07", "贰拾万元零柒分"),
        ("1000100", "壹佰万零壹佰元整"),
    ],
)
def test_amount_to_chinese_upper(amount: str, expected: str) -> None:
    assert amount_to_chinese_upper(Decimal(amount)) == expected


def test_uscc_check_digit_roundtrip() -> None:
    """生成的代码必须通过自己的校验；改一位后必须失败。"""
    rng = random.Random(20261118)
    for _ in range(20):
        code = gen_uscc(rng)
        assert is_valid_uscc(code), code
        assert uscc_check_digit(code[:17]) == code[17]
    good = gen_uscc(random.Random(1))
    tampered = good[:10] + ("0" if good[10] != "0" else "1") + good[11:]
    assert not is_valid_uscc(tampered)


def test_invoice_code_and_no_format() -> None:
    rng = random.Random(7)
    for _ in range(20):
        code = gen_invoice_code(rng)
        assert len(code) in (10, 12) and code.isdigit()
        number = gen_invoice_no(rng)
        assert len(number) == 8 and number.isdigit()


def test_id_card_checksum_valid() -> None:
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_chars = "10X98765432"
    rng = random.Random(3)
    for _ in range(10):
        card = gen_id_card(rng)
        total = sum(int(ch) * w for ch, w in zip(card[:17], weights))
        assert card[17] == check_chars[total % 11]


def test_money_text_has_thousand_separator() -> None:
    assert money_text(Decimal("1234.56")) == "¥1,234.56"
    assert money_text(Decimal("0.05")) == "¥0.05"
