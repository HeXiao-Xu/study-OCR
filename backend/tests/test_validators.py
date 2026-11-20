"""S1-5 校验器单测（任务卡要求：每个校验器都要有通过与失败样例）。

金额一律 Decimal。这几个校验器是"识别结果可信度"的最后一道闸，
出错的表现是"错的票被判成对的"，比识别不出更危险。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.ocr.validators import (
    USCC_CHARSET,
    parse_date,
    parse_money,
    parse_upper_amount,
    run_validators,
    validate_date,
    validate_invoice_code,
    validate_invoice_no,
    validate_money_sum,
    validate_upper_amount,
    validate_uscc,
)

# 合法样例（GB 32100 校验位正确，由 S1-1 的生成器产出）
VALID_USCC = "9142BBUJA57H9NHHCW"


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("text", "expected"), [
    ("¥1,234.56", "1234.56"),
    ("￥408.30", "408.30"),
    ("金额：¥36.00", "36.00"),
    ("１２３．４５", "123.45"),        # 全角
    ("0.05", "0.05"),
])
def test_parse_money_handles_real_formats(text: str, expected: str) -> None:
    assert parse_money(text) == Decimal(expected)


def test_parse_money_returns_none_for_noise() -> None:
    assert parse_money("") is None
    assert parse_money("人民币") is None
    assert parse_money(None) is None


def test_parse_money_prefers_longest_candidate() -> None:
    """`¥408.30` 里同时有 `408.30` 与 `408`，必须取长的那个（否则金额丢两位小数）。"""
    assert parse_money("¥408.30") == Decimal("408.30")


@pytest.mark.parametrize(("text", "expected"), [
    ("2025年11月04日", date(2025, 11, 4)),
    ("开票日期：2026-03-11", date(2026, 3, 11)),
    ("2025/12/30", date(2025, 12, 30)),
])
def test_parse_date_formats(text: str, expected: date) -> None:
    assert parse_date(text) == expected


def test_parse_date_invalid() -> None:
    assert parse_date("乱码日期") is None
    assert parse_date("2025年13月40日") is None      # 月份/日期越界


@pytest.mark.parametrize(("text", "expected"), [
    ("壹仟贰佰叁拾肆元伍角陆分", "1234.56"),
    ("零元零伍分", "0.05"),
    ("壹万元整", "10000.00"),
    ("玖佰贰拾贰元柒角伍分", "922.75"),
    ("贰拾万元零柒分", "200000.07"),
])
def test_parse_upper_amount(text: str, expected: str) -> None:
    assert parse_upper_amount(text) == Decimal(expected)


def test_parse_upper_amount_rejects_non_amount() -> None:
    assert parse_upper_amount("壹佰元") is None or parse_upper_amount("壹佰元") == Decimal("100.00")


# ---------------------------------------------------------------------------
# 金额三件套
# ---------------------------------------------------------------------------
def test_money_sum_matches() -> None:
    assert validate_money_sum(Decimal("184.80"), Decimal("11.09"), Decimal("195.89")) is None


def test_money_sum_within_tolerance() -> None:
    assert validate_money_sum(Decimal("184.80"), Decimal("11.09"), Decimal("195.90")) is None


def test_money_sum_mismatch() -> None:
    issue = validate_money_sum(Decimal("184.80"), Decimal("11.09"), Decimal("196.89"))
    assert issue is not None and issue.code == "money_sum_mismatch"


def test_money_sum_missing_values_are_skipped() -> None:
    assert validate_money_sum(None, Decimal("1"), Decimal("2")) is None
    assert validate_money_sum(Decimal("1"), None, None) is None


def test_upper_amount_consistency() -> None:
    assert validate_upper_amount("壹佰玖拾伍元捌角玖分", Decimal("195.89")) is None
    issue = validate_upper_amount("壹佰玖拾伍元捌角玖分", Decimal("195.88"))
    assert issue is not None and issue.code == "upper_amount_mismatch"


def test_upper_amount_accepts_numeric_string() -> None:
    """抽取器会把大写金额归一化成数值字符串，校验器要能接住。"""
    assert validate_upper_amount("195.89", Decimal("195.89")) is None


# ---------------------------------------------------------------------------
# 日期
# ---------------------------------------------------------------------------
def test_date_in_future_is_rejected() -> None:
    today = date(2026, 11, 19)
    issue = validate_date(today + timedelta(days=30), today=today)
    assert issue is not None and issue.code == "date_in_future"


def test_date_today_is_allowed() -> None:
    today = date(2026, 11, 19)
    assert validate_date(today, today=today) is None


# ---------------------------------------------------------------------------
# 发票代码 / 号码
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", ["1234567890", "123456789012"])
def test_invoice_code_valid(code: str) -> None:
    assert validate_invoice_code(code) is None


@pytest.mark.parametrize("code", ["123456789", "1234567890123", "12AB567890"])
def test_invoice_code_invalid(code: str) -> None:
    issue = validate_invoice_code(code)
    assert issue is not None and issue.code == "invalid_invoice_code"


def test_invoice_no_length_depends_on_ticket_type() -> None:
    assert validate_invoice_no("17125473") is None
    assert validate_invoice_no("26132000003436703626", "vat_digital") is None
    # 8 位号码放进数电票 → 必须报错（数电票是 20 位）
    issue = validate_invoice_no("17125473", "vat_digital")
    assert issue is not None and issue.code == "invalid_invoice_no"


# ---------------------------------------------------------------------------
# 统一社会信用代码
# ---------------------------------------------------------------------------
def test_uscc_valid() -> None:
    assert validate_uscc(VALID_USCC) is None


def test_uscc_detects_single_character_change() -> None:
    """任务卡验收：故意改一位税号必须报错。"""
    for position in (0, 8, 16, 17):
        mutate_to = "0" if VALID_USCC[position] != "0" else "1"
        broken = VALID_USCC[:position] + mutate_to + VALID_USCC[position + 1:]
        issue = validate_uscc(broken)
        assert issue is not None, f"位置 {position} 改动后未被发现：{broken}"


def test_uscc_rejects_illegal_charset() -> None:
    broken = "I" + VALID_USCC[1:]
    issue = validate_uscc(broken)
    assert issue is not None and issue.code == "invalid_uscc_charset"


def test_uscc_rejects_wrong_length() -> None:
    issue = validate_uscc(VALID_USCC[:17])
    assert issue is not None and issue.code == "invalid_uscc"


def test_uscc_charset_excludes_confusing_letters() -> None:
    for char in "IOSVZ":
        assert char not in USCC_CHARSET


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def test_run_validators_reports_failed_fields() -> None:
    from app.ocr.types import ExtractedField

    fields = {
        "total_amount": ExtractedField(name="total_amount", value="196.89", raw_text="¥196.89", confidence=0.9),
        "amount": ExtractedField(name="amount", value="184.80", raw_text="", confidence=0.9),
        "tax_amount": ExtractedField(name="tax_amount", value="11.09", raw_text="", confidence=0.9),
    }
    result = run_validators(fields, "vat_general")
    assert not result.ok
    assert "total_amount" in result.failed_fields()
    assert "money_sum_mismatch" in result.codes()


def test_run_validators_ok_on_consistent_values() -> None:
    from app.ocr.types import ExtractedField

    fields = {
        "total_amount": ExtractedField(name="total_amount", value="195.89", raw_text="", confidence=0.9),
        "amount": ExtractedField(name="amount", value="184.80", raw_text="", confidence=0.9),
        "tax_amount": ExtractedField(name="tax_amount", value="11.09", raw_text="", confidence=0.9),
        "invoice_date": ExtractedField(name="invoice_date", value="2026-03-11", raw_text="", confidence=0.9),
        "buyer_tax_no": ExtractedField(name="buyer_tax_no", value=VALID_USCC, raw_text="", confidence=0.9),
    }
    assert run_validators(fields, "vat_general").ok