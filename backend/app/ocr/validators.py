"""字段校验器（S1-5）。

**全部是纯函数**：输入字段值，输出问题列表；不抛异常、不改数据、不查库。
"抽取器不许静默修正冲突值"（AGENTS §10.3）——校验只负责**报告**，改不改由用户在前端决定。

金额一律 `Decimal`（float 在 0.01 容差处会误判，任务卡明确的坑）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

__all__ = [
    "ValidationIssue", "ValidationResult",
    "parse_money", "parse_date", "parse_upper_amount",
    "validate_money_sum", "validate_upper_amount", "validate_date",
    "validate_invoice_code", "validate_invoice_no", "validate_uscc",
    "run_validators", "USCC_CHARSET", "USCC_WEIGHTS",
]

# GB 32100-2015：字符集剔除 I O S V Z，共 31 个字符
USCC_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]
MONEY_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    code: str          # 机器可读：money_sum_mismatch / invalid_uscc / ...
    message: str       # 给人看的中文说明
    severity: str = "error"     # error 会把该字段置信度折半；warning 只提示


@dataclass
class ValidationResult:
    issues: list[ValidationIssue]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def failed_fields(self) -> set[str]:
        return {issue.field for issue in self.errors}

    def codes(self) -> list[str]:
        return [issue.code for issue in self.issues]


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
_FULLWIDTH = str.maketrans("０１２３４５６７８９．，－", "0123456789.,-")
# 整数部分允许任意位：写成 \d{1,3} 时 "1547.37" 会被切成 "154" + "7.37"（逗号被合并后触发）
_MONEY_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d{1,2})?")


def parse_money(text: str | Decimal | None) -> Decimal | None:
    """把票面金额文本转成 Decimal。识别 ¥/￥、千分位、全角数字、前后噪声。

    同一段文本里有多个候选时**取最长的那个**（`¥408.30` 里的 `408.30` 比 `408` 长），
    不取"最后一行"这种模糊规则。
    """
    if text is None:
        return None
    if isinstance(text, Decimal):
        return text
    cleaned = str(text).translate(_FULLWIDTH)
    # 千分位被 OCR 读成空格很常见（"¥1 547.37"）；先把"3 位一组"的空格/逗号合并，
    # 否则会把 1547.37 解析成 547.37（实测训练票的票价就这样错了）。
    cleaned = re.sub(r"(?<=\d)[\s,](?=\d{3}(?:[.,]\d{2})?(?!\d))", "", cleaned)
    candidates = _MONEY_RE.findall(cleaned.replace("¥", " ").replace("￥", " "))
    candidates = [item for item in candidates if item not in {"", "-"}]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: (len(item.replace(",", "")), item))
    try:
        return Decimal(best.replace(",", ""))
    except InvalidOperation:
        return None


_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})"),
]


def parse_date(text: str | None) -> date | None:
    """从票面文本里解析日期（支持 2025年11月04日 / 2025-11-04 / 2025/11/04）。"""
    if not text:
        return None
    for pattern in _DATE_PATTERNS:
        match = pattern.search(str(text))
        if not match:
            continue
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


_CN_DIGITS = {"零": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
              "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"拾": 10, "佰": 100, "仟": 1000, "十": 10, "百": 100, "千": 1000}


def parse_upper_amount(text: str | None) -> Decimal | None:
    """解析中文大写金额（壹仟贰佰叁拾肆元伍角陆分 → 1234.56）。

    支持"整/正"结尾、中间补"零"。解析不动就返回 None（宁可判不出，不要瞎猜）。
    """
    if not text:
        return None
    cleaned = str(text).strip()
    match = re.search(r"([零壹贰叁肆伍陆柒捌玖拾佰仟万亿元圆角分整正一二三四五六七八九十百千]+)", cleaned)
    if not match:
        return None
    body = match.group(1)
    if "元" not in body and "圆" not in body:
        return None
    integer_part, _, fraction_part = re.split(r"([元圆])", body, maxsplit=1)
    fraction_part = (fraction_part or "").replace("整", "").replace("正", "")

    total = Decimal(0)
    section = 0        # 当前"万"以内的部分
    current = 0        # 当前数字
    for char in integer_part:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            section += (current or 1) * _CN_UNITS[char]
            current = 0
        elif char == "万":
            section = (section + current) * 10000
            total += section
            section, current = 0, 0
        elif char == "亿":
            total = (total + section + current) * 100000000
            section, current = 0, 0
        elif char in "零":
            current = 0
    total += section + current

    fraction = Decimal(0)
    jiao = re.search(r"([零壹贰叁肆伍陆柒捌玖])角", fraction_part)
    fen = re.search(r"([零壹贰叁肆伍陆柒捌玖])分", fraction_part)
    if jiao:
        fraction += Decimal(_CN_DIGITS[jiao.group(1)]) / Decimal(10)
    if fen:
        fraction += Decimal(_CN_DIGITS[fen.group(1)]) / Decimal(100)
    return (total + fraction).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# 校验器
# ---------------------------------------------------------------------------
def validate_money_sum(amount: Decimal | None, tax: Decimal | None, total: Decimal | None,
                       tolerance: Decimal = MONEY_TOLERANCE) -> ValidationIssue | None:
    """价税合计 = 金额 + 税额（三项都缺或部分缺时不做判断）。"""
    if total is None or amount is None:
        return None
    if tax is None:
        # 只有金额和合计时，至少要求 合计 ≥ 金额
        if total < amount:
            return ValidationIssue("total_amount", "money_sum_mismatch",
                                   f"价税合计 {total} 小于金额 {amount}")
        return None
    if abs(total - (amount + tax)) > tolerance:
        return ValidationIssue("total_amount", "money_sum_mismatch",
                               f"价税合计 {total} ≠ 金额 {amount} + 税额 {tax} = {amount + tax}")
    return None


def validate_upper_amount(upper_text: str | None, total: Decimal | None,
                          tolerance: Decimal = Decimal("0")) -> ValidationIssue | None:
    """大写与小写必须**完全一致**（容差 0）：这不涉及四舍五入，差 0.01 就是读错了。"""
    if not upper_text or total is None:
        return None
    parsed = parse_upper_amount(upper_text)
    if parsed is None:
        # 抽取器可能已经把大写金额归一化成数值字符串，这种情况直接按数值比
        parsed = parse_money(upper_text)
    if parsed is None:
        return ValidationIssue("total_amount", "upper_amount_unparsable", f"大写金额无法解析：{upper_text}")
    if abs(parsed - total) > tolerance:
        return ValidationIssue("total_amount", "upper_amount_mismatch",
                               f"大写金额 {parsed} 与小写 {total} 不一致")
    return None


def validate_date(value: str | date | None, today: date | None = None) -> ValidationIssue | None:
    today = today or date.today()
    if value is None:
        return None
    parsed = value if isinstance(value, date) else parse_date(str(value))
    if parsed is None:
        return ValidationIssue("invoice_date", "unparsable_date", f"日期无法解析：{value}")
    if parsed > today:
        return ValidationIssue("invoice_date", "date_in_future", f"开票日期 {parsed} 晚于今天 {today}")
    if parsed.year < 2000:
        return ValidationIssue("invoice_date", "date_too_old", f"开票日期 {parsed} 早于 2000 年")
    return None


def validate_invoice_code(value: str | None) -> ValidationIssue | None:
    if not value:
        return None
    digits = re.sub(r"\s", "", str(value))
    if not re.fullmatch(r"\d{10}|\d{12}", digits):
        return ValidationIssue("invoice_code", "invalid_invoice_code", f"发票代码应为 10 或 12 位数字：{value}")
    return None


def validate_invoice_no(value: str | None, ticket_type: str | None = None) -> ValidationIssue | None:
    """发票号码：数电票 20 位；火车票新旧两种（8 位票号 / 20 位电子客票号）；其余 8 位。"""
    if not value:
        return None
    digits = re.sub(r"\s", "", str(value))
    if ticket_type == "vat_digital":
        pattern, expected = r"\d{20}", "20"
    elif ticket_type == "train_ticket":
        # 铁路电子客票的发票号码是 20 位（26539149820000052808 这类）；老票仍可能是 8 位
        pattern, expected = r"\d{8}|\d{20}", "8 或 20"
    else:
        pattern, expected = r"\d{8}", "8"
    if not re.fullmatch(pattern, digits):
        return ValidationIssue("invoice_no", "invalid_invoice_no",
                               f"发票号码应为 {expected} 位数字：{value}")
    return None


def validate_uscc(value: str | None) -> ValidationIssue | None:
    """统一社会信用代码：18 位、字符集剔除 I/O/S/V/Z、GB 32100 校验位。"""
    if not value:
        return None
    code = re.sub(r"[\s\-]", "", str(value)).upper()
    if len(code) != 18:
        return ValidationIssue("tax_no", "invalid_uscc", f"统一社会信用代码应为 18 位：{value}")
    invalid_chars = [char for char in code if char not in USCC_CHARSET]
    if invalid_chars:
        return ValidationIssue("tax_no", "invalid_uscc_charset",
                               f"含非法字符 {''.join(invalid_chars)}（不含 I/O/S/V/Z）：{value}")
    total = sum(USCC_CHARSET.index(char) * weight for char, weight in zip(code[:17], USCC_WEIGHTS))
    remainder = 31 - total % 31
    expected = USCC_CHARSET[0 if remainder == 31 else remainder]
    if expected != code[17]:
        return ValidationIssue("tax_no", "invalid_uscc_checksum",
                               f"校验位应为 {expected}，实际 {code[17]}：{value}")
    return None


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def run_validators(fields: dict, ticket_type: str | None = None,
                   today: date | None = None) -> ValidationResult:
    """对抽取结果跑全部校验。

    `fields` 是 {字段名: 值或带 .value 的对象}；缺失字段不报错（由抽取器给 warning）。
    税号校验按 buyer/seller 分别报，便于前端定位到具体字段。
    """
    def value_of(name: str):
        item = fields.get(name)
        return getattr(item, "value", item)

    issues: list[ValidationIssue] = []

    amount = parse_money(value_of("amount"))
    tax = parse_money(value_of("tax_amount"))
    total = parse_money(value_of("total_amount"))
    issues.append(validate_money_sum(amount, tax, total))

    upper = value_of("total_amount_upper") or value_of("total_amount_in_words")
    issue = validate_upper_amount(upper, total)
    if issue:
        issues.append(issue)

    issues.append(validate_date(value_of("invoice_date"), today))
    issues.append(validate_invoice_code(value_of("invoice_code")))
    issues.append(validate_invoice_no(value_of("invoice_no"), ticket_type))

    for name, label in (("buyer_tax_no", "购买方税号"), ("seller_tax_no", "销售方税号")):
        issue = validate_uscc(value_of(name))
        if issue:
            issues.append(ValidationIssue(name, issue.code, f"{label}{issue.message}", issue.severity))

    return ValidationResult([issue for issue in issues if issue is not None])