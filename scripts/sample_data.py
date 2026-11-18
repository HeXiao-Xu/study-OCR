#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""票面字段值生成（S1-1）。

铁律：**所有字段值都必须合法**。合成数据的价值就在于"标注绝对正确"，
如果生成的税号校验位是错的、金额三件套对不上，S1-5 的校验规则就无从验证，
评测出来的"抽取准确率"也没有意义。

这里刻意保持纯函数 + 显式 rng，便于固定种子复现。
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

# ---------------------------------------------------------------------------
# 中文大写金额
# ---------------------------------------------------------------------------
_CN_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_CN_UNITS = ["", "拾", "佰", "仟"]
_CN_GROUPS = ["", "万", "亿", "万亿"]


def _four_digits(n: int) -> str:
    """0<n<10000 → 壹仟贰佰叁拾肆（内部连续 0 折叠成一个"零"）。"""
    text = ""
    zero_pending = False
    digits = f"{n:04d}"
    for i, ch in enumerate(digits):
        value = int(ch)
        if value == 0:
            zero_pending = bool(text)
            continue
        if zero_pending:
            text += "零"
            zero_pending = False
        text += _CN_DIGITS[value] + _CN_UNITS[3 - i]
    return text


def _integer_part(n: int) -> str:
    if n == 0:
        return ""
    groups: list[int] = []
    while n > 0:
        groups.append(n % 10000)
        n //= 10000

    parts: list[str] = []
    for idx in range(len(groups) - 1, -1, -1):
        group = groups[idx]
        if group == 0:
            # 整组为零：只在后面还有非零组时留一个"零"
            if parts and idx > 0 and any(groups[:idx]):
                if not parts[-1].endswith("零"):
                    parts.append("零")
            continue
        # 低组不足四位且前面已有内容，需要补"零"（如 100万零100）
        if parts and group < 1000 and not parts[-1].endswith("零"):
            parts.append("零")
        parts.append(_four_digits(group) + _CN_GROUPS[idx])
    return "".join(parts)


def amount_to_chinese_upper(amount: Decimal | str | int | float) -> str:
    """1234.56 → 壹仟贰佰叁拾肆元伍角陆分；100.10 → 壹佰元壹角整；1000 → 壹仟元整。"""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = value < 0
    value = abs(value)
    integer = int(value)
    cents = int((value - integer) * 100)
    jiao, fen = divmod(cents, 10)

    if integer == 0 and cents == 0:
        return "零元整"

    text = (_integer_part(integer) + "元") if integer else "零元"
    if jiao == 0 and fen == 0:
        text += "整"
    else:
        if jiao == 0:
            text += "零"          # 10010.05 → 壹万零壹拾元零伍分；0.05 → 零元零伍分
        else:
            text += _CN_DIGITS[jiao] + "角"
        text += (_CN_DIGITS[fen] + "分") if fen else "整"
    return ("负" + text) if negative else text


# ---------------------------------------------------------------------------
# 统一社会信用代码（GB 32100-2015）
# ---------------------------------------------------------------------------
_USCC_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"  # 剔除 I O S V Z
_USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def uscc_check_digit(first17: str) -> str:
    total = sum(_USCC_CHARSET.index(ch) * w for ch, w in zip(first17, _USCC_WEIGHTS))
    remainder = 31 - total % 31
    return _USCC_CHARSET[0 if remainder == 31 else remainder]


def is_valid_uscc(code: str) -> bool:
    if len(code) != 18 or any(ch not in _USCC_CHARSET for ch in code):
        return False
    return uscc_check_digit(code[:17]) == code[17]


def gen_uscc(rng: random.Random) -> str:
    """生成**校验位合法**的 18 位统一社会信用代码。"""
    body = ["9", "1", "4", "2"]  # 91 + 湖北(42)
    body += [rng.choice(_USCC_CHARSET) for _ in range(13)]
    first17 = "".join(body)
    return first17 + uscc_check_digit(first17)


# ---------------------------------------------------------------------------
# 其他字段
# ---------------------------------------------------------------------------
def gen_digital_invoice_no(rng: random.Random) -> str:
    """数电票的 20 位发票号码（前两位为年份后两位，如 26xxxxxxxxxxxxxxxxxx）。"""
    return "26" + "".join(rng.choice("0123456789") for _ in range(18))


def gen_invoice_code(rng: random.Random) -> str:
    width = rng.choice([10, 12])
    return "".join(rng.choice("0123456789") for _ in range(width))


def gen_invoice_no(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789") for _ in range(8))


def gen_check_code(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789") for _ in range(20))


def gen_date(rng: random.Random, *, start: date = date(2025, 1, 1), end: date = date(2026, 11, 1)) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def gen_amounts(rng: random.Random) -> dict[str, Decimal]:
    """金额三件套：amount + tax_amount = total_amount（严格相等，不用容差）。"""
    amount = (Decimal(rng.randint(1, 200000)) / 100).quantize(Decimal("0.01"))
    rate = rng.choice([Decimal("0.06"), Decimal("0.13"), Decimal("0.03"), Decimal("0")])
    tax = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"amount": amount, "tax_amount": tax, "total_amount": amount + tax}


_COMPANY_PREFIX = ["武汉", "湖北", "华中", "长江", "光谷", "南湖", "东湖", "汉阳"]
_COMPANY_MIDDLE = ["宏图", "瑞达", "金穗", "新元", "博远", "华信", "启明", "中兴"]
_COMPANY_SUFFIX = ["商贸有限公司", "科技有限公司", "餐饮管理有限公司", "印务有限公司", "旅游服务有限公司"]

_PERSON_NAMES = ["张伟", "李娜", "王芳", "刘洋", "陈晨", "杨帆", "赵磊", "周敏", "吴强", "徐静"]

_SELLER_TAXI = ["武汉市出租车汽车有限公司", "湖北长江出租汽车有限公司", "武汉华昌出租汽车有限公司"]


def gen_company(rng: random.Random) -> str:
    return f"{rng.choice(_COMPANY_PREFIX)}{rng.choice(_COMPANY_MIDDLE)}{rng.choice(_COMPANY_SUFFIX)}"


def gen_person(rng: random.Random) -> str:
    """只用固定假名池——真实姓名绝不能进数据集。"""
    return rng.choice(_PERSON_NAMES)


def gen_taxi_company(rng: random.Random) -> str:
    return rng.choice(_SELLER_TAXI)


def gen_id_card(rng: random.Random) -> str:
    """18 位、校验位合法的**虚构**身份证号（前 6 位用 420111，非真实归属组合）。"""
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_chars = "10X98765432"
    body = "420111" + "".join(rng.choice("0123456789") for _ in range(8)) + "00" + "".join(
        rng.choice("0123456789") for _ in range(3)
    )
    body = body[:17]
    total = sum(int(ch) * w for ch, w in zip(body, weights))
    return body + check_chars[total % 11]


def gen_plate(rng: random.Random) -> str:
    return "鄂A" + "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5))


def gen_train_no(rng: random.Random) -> str:
    return rng.choice(["G", "D", "Z", "T", "K"]) + str(rng.randint(1, 9999))


def gen_flight_no(rng: random.Random) -> str:
    return rng.choice(["MU", "CZ", "CA", "HU", "ZH"]) + str(rng.randint(1000, 9999))


def money_text(value: Decimal) -> str:
    """票面原样：带千分位与货币符号，如 ¥1,234.56。"""
    return "¥{:,.2f}".format(value)


def date_text(value: date) -> str:
    return f"{value.year}年{value.month:02d}月{value.day:02d}日"


def date_iso(value: date) -> str:
    return value.isoformat()