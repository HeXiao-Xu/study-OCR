"""S1-5 抽取器单测（不加载模型，用构造的 OcrLine 列表）。

锁三件事：
① 锚点优先级与取值位置（合计行的"金额/税额"两列）；
② **冲突不静默替换**（同优先级证据矛盾时保留 candidates）；
③ 缺失必填字段必须进 `missing_required`，校验失败必须把置信度封顶到 0.45（S3-2）。
"""
from __future__ import annotations

import pytest

from app.ocr.extractor import extract_fields, load_field_rules
from app.ocr.types import OcrLine

VALID_USCC = "9142BBUJA57H9NHHCW"


def _line(text: str, y: float, line_no: int, *, x: float = 40.0, height: float = 26.0,
          score: float = 0.98) -> OcrLine:
    return OcrLine(text=text, score=score,
                   box=[[x, y - height / 2], [x + 700, y - height / 2],
                        [x + 700, y + height / 2], [x, y + height / 2]],
                   line_no=line_no, y_center=y, x_left=x, height=height)


def _digital_lines() -> list[OcrLine]:
    return [
        _line("电子发票（普通发票）", 50, 0),
        _line("发票号码：26132000003436703626", 90, 1),
        _line("开票日期：2025年11月04日", 120, 2),
        _line("购买方名称：武汉金穗商贸有限公司", 200, 3),
        _line(f"纳税人识别号：{VALID_USCC}", 230, 4),
        _line("销售方名称：华中启明印务有限公司", 300, 5),
        _line("纳税人识别号：9142PBJTDT69G7A57J", 330, 6),
        _line("合计  ¥184.80  ¥11.09", 500, 7),
        _line("价税合计（大写）壹佰玖拾伍元捌角玖分", 560, 8),
        _line("（小写）¥195.89", 590, 9),
    ]


def test_rules_load_all_fields() -> None:
    rules = load_field_rules()
    names = {rule.name for rule in rules}
    assert {"invoice_code", "invoice_no", "invoice_date", "amount", "tax_amount",
            "total_amount", "check_code", "buyer_tax_no"} <= names


def test_extract_digital_invoice_fields() -> None:
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    values = {name: field.value for name, field in result.fields.items()}

    assert values["invoice_no"] == "26132000003436703626"
    assert values["invoice_date"] == "2025-11-04"
    assert values["buyer_name"] == "武汉金穗商贸有限公司"
    assert values["buyer_tax_no"] == VALID_USCC
    assert values["seller_name"] == "华中启明印务有限公司"
    assert values["amount"] == "184.80"
    assert values["tax_amount"] == "11.09"
    assert values["total_amount"] == "195.89"
    assert result.missing_required == []
    # 字段必须能回指来源行（前端 hover 高亮 / 审计回溯都靠它）
    assert result.fields["total_amount"].source_line_no == 9


def test_amount_and_tax_come_from_the_same_total_row_by_position() -> None:
    """合计行形如"合计 ¥184.80 ¥11.09"：金额取第 0 个、税额取第 1 个（value_index）。"""
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    assert result.fields["amount"].value == "184.80"
    assert result.fields["tax_amount"].value == "11.09"
    assert result.fields["amount"].source_line_no == result.fields["tax_amount"].source_line_no


def test_total_amount_prefers_the_largest_candidate() -> None:
    """多处候选时按显式规则取最大，而不是"取最后一行"。"""
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    assert result.fields["total_amount"].value == "195.89"


def test_same_priority_conflict_keeps_candidates() -> None:
    """两个同优先级锚点给出不同值 → 记冲突并保留候选，绝不静默替换。"""
    # 同一锚点（"发票号码"）在票面出现两次且值不同 —— 这才是真正的同优先级矛盾
    # （不同锚点之间是**优先级**关系，高优先级直接胜出，不算冲突）
    lines = [
        _line("发票号码：12345678", 90, 0),
        _line("发票号码：87654321", 700, 1),
        _line("开票日期：2025年11月04日", 120, 2),
        _line("价税合计：¥100.00", 300, 3),
    ]
    result = extract_fields(lines, (1200, 800), "vat_general")
    conflict_fields = {item["field"] for item in result.conflicts}
    assert "invoice_no" in conflict_fields
    field = result.fields["invoice_no"]
    assert len(field.candidates) >= 2
    assert set(field.candidates) >= {"12345678", "87654321"}


def test_missing_required_fields_are_reported() -> None:
    lines = [_line("发票号码：12345678", 90, 0)]
    result = extract_fields(lines, (1200, 800), "vat_general")
    assert "invoice_date" in result.missing_required
    assert "total_amount" in result.missing_required


def test_invalid_uscc_caps_confidence_below_half() -> None:
    broken = VALID_USCC[:10] + ("0" if VALID_USCC[10] != "0" else "1") + VALID_USCC[11:]
    lines = [
        _line("发票号码：12345678", 90, 0),
        _line("开票日期：2025年11月04日", 120, 1),
        _line("价税合计：¥100.00", 300, 2),
        _line(f"纳税人识别号：{broken}", 330, 3),
    ]
    result = extract_fields(lines, (1200, 800), "vat_general")
    field = result.fields["buyer_tax_no"]
    assert field.confidence <= 0.45            # 校验失败封顶（任务卡"直接 <0.5"）
    assert any("invalid_uscc" in warning for warning in result.warnings)


def test_anchor_hit_records_confidence_components() -> None:
    """S3-2：抽取器只记录证据，档位由 confidence.py 统一判。"""
    from app.services.confidence import level_for

    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    field = result.fields["invoice_no"]

    assert field.anchor_score is not None and field.value_score is not None
    assert field.anchor_score == pytest.approx(0.98)
    assert field.value_score == pytest.approx(0.98)
    assert field.agreement == 1.0                      # 同行取值
    assert level_for(field.confidence) in {"auto", "review", "manual"}


def test_derived_field_has_no_ocr_components() -> None:
    """推导值没有锚点行/值行，不能假装有 OCR 证据。

    触发方式：把 total_amount 的锚点改成一个必然不存在的词，强制走"amount + tax = total"推导分支
    （真实票面上 total 的锚点与 amount/tax 重叠，正常样本不会走到这里）。
    """
    from dataclasses import replace

    lines = [
        _line("电子发票（普通发票）", 50, 0),
        _line("发票号码：12345678", 90, 1),
        _line("开票日期：2025年11月04日", 120, 2),
        _line("金额  ¥184.80", 480, 3),
        _line("税额  ¥11.09", 520, 4),
    ]
    rules = [
        replace(rule, anchors=["不存在的锚点"]) if rule.name == "total_amount" else rule
        for rule in load_field_rules()
        if rule.name in {"amount", "tax_amount", "total_amount"}
    ]

    result = extract_fields(lines, (1200, 800), "vat_digital", rules=rules)

    derived = result.fields["total_amount"]
    assert derived.rule_id == "derived:total_amount"
    assert derived.value == "195.89"
    assert derived.anchor_score is None and derived.value_score is None


def test_ticket_specific_rules_are_skipped() -> None:
    """check_code 只对旧版票有效：数电票里就算出现"校验码"也不该抽。"""
    lines = _digital_lines() + [_line("校验码：12345678901234567890", 700, 10)]
    digital = extract_fields(lines, (1200, 800), "vat_digital")
    assert "check_code" not in digital.fields

    legacy = extract_fields(lines, (1200, 800), "vat_general")
    assert "check_code" in legacy.fields


def test_date_normalized_to_iso() -> None:
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    assert result.fields["invoice_date"].value == "2025-11-04"
    assert "2025年" in result.fields["invoice_date"].raw_text


def test_empty_lines_do_not_raise() -> None:
    result = extract_fields([], (1200, 800), "unknown")
    assert result.fields == {}
    assert set(result.missing_required) >= {"invoice_no", "invoice_date", "total_amount"}


def test_rules_from_specs_matches_yaml_rules() -> None:
    """DB 版规则（`field_specs_json`）与 YAML 版必须逐字段等价，否则两套来源会悄悄漂移。

    S3-1 起流水线优先读库（S2-8 既定方向），YAML 只作回退；两条路给出不同规则 = 识别结果随
    "有没有 seed 过库"变化，这是最隐蔽的一类不一致。
    """
    from app.ocr.extractor import rules_from_specs
    from app.services.template_seed import build_seed_templates

    yaml_rules = {rule.name: rule for rule in load_field_rules()}
    seed = next(item for item in build_seed_templates() if item.type_code == "vat_digital")
    db_rules = {rule.name: rule for rule in rules_from_specs(seed.field_specs_json)}

    assert db_rules, "vat_digital 的 DB 规则不能为空"
    for name, db_rule in db_rules.items():
        expected = yaml_rules[name]
        assert db_rule == expected, f"{name} 的 DB 规则与 YAML 不一致"


def test_rules_from_specs_ignores_non_dict_specs() -> None:
    from app.ocr.extractor import rules_from_specs

    assert rules_from_specs({"broken": "not-a-dict"}) == []
    assert rules_from_specs({}) == []

# --------------------------------------------------------------------------- #
# S3-4：保留票面原文（raw_value）与归一值分开
# --------------------------------------------------------------------------- #
def test_money_field_keeps_raw_value_and_normalized_value() -> None:
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    field = result.fields["total_amount"]
    assert field.value == "195.89"          # 归一值
    assert field.raw_value == "¥195.89"     # 票面原文（带货币符号）
    assert field.raw_value != field.value


def test_date_field_raw_value_keeps_document_form() -> None:
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    field = result.fields["invoice_date"]
    assert field.value == "2025-11-04"
    # raw 是规则正则命中的原文片段（regex 不含尾缀"日"，故到这里为止；值仍是完整 ISO 日期）
    assert field.raw_value == "2025年11月04"


def test_labeled_name_field_raw_value_is_the_value_part() -> None:
    result = extract_fields(_digital_lines(), (1200, 800), "vat_digital")
    field = result.fields["buyer_name"]
    assert field.value.startswith("武汉金穗")
    assert field.raw_value == "武汉金穗商贸有限公司"


def test_whole_line_name_field_keeps_raw_value() -> None:
    lines = [_line("武汉市出租汽车有限公司", 300, 0)]
    result = extract_fields(lines, (1200, 800), "taxi_ticket")
    field = result.fields["seller_name"]
    assert field.value == "武汉市出租汽车有限公司"
    assert field.raw_value == "武汉市出租汽车有限公司"


def test_derived_field_has_no_raw_value() -> None:
    from dataclasses import replace

    lines = [
        _line("电子发票（普通发票）", 50, 0),
        _line("发票号码：12345678", 90, 1),
        _line("开票日期：2025年11月04日", 120, 2),
        _line("金额  ¥184.80", 480, 3),
        _line("税额  ¥11.09", 520, 4),
    ]
    rules = [
        replace(rule, anchors=["不存在的锚点"]) if rule.name == "total_amount" else rule
        for rule in load_field_rules()
        if rule.name in {"amount", "tax_amount", "total_amount"}
    ]

    result = extract_fields(lines, (1200, 800), "vat_digital", rules=rules)

    derived = result.fields["total_amount"]
    assert derived.rule_id == "derived:total_amount"
    assert derived.raw_value is None
    assert derived.value == "195.89"


def test_neighbour_value_returns_raw_and_normalized() -> None:
    """跨行取值（同行右侧）也要带原文，不能只回归一值。"""
    from app.ocr.extractor import _neighbour_value

    lines = [
        _line("开票日期", 300, 0),
        _line("2025年11月04日", 300, 1, x=200.0),
    ]
    rules = {rule.name: rule for rule in load_field_rules()}

    matched = _neighbour_value(rules["invoice_date"], lines[0], lines, 800)

    assert matched is not None
    raw, value, line_no = matched
    assert value == "2025-11-04"
    assert raw == "2025年11月04"
    assert line_no == 1


# --------------------------------------------------------------------------- #
# S3-9 真实横版票回归：金额/税额不能被"价税合计"行抢走；税号 O→0
# --------------------------------------------------------------------------- #
def _line_at(text: str, y: float, line_no: int, *, x: float, height: float = 30.0) -> OcrLine:
    return OcrLine(
        text=text, score=0.98,
        box=[[x, y - height / 2], [x + 520, y - height / 2],
             [x + 520, y + height / 2], [x, y + height / 2]],
        line_no=line_no, y_center=y, x_left=x, height=height,
    )


def test_landscape_table_amount_not_stolen_by_total_line() -> None:
    """真实横版数电票（115）：金额 1057.43 / 税额 10.57 / 合计 1068.00。"""
    lines = [
        _line_at("金额", 540, 22, x=1411),
        _line_at("税率/征收率", 539, 23, x=1547),
        _line_at("税额", 540, 24, x=1915),
        _line_at("1057.43", 576, 26, x=1395),
        _line_at("10.57", 576, 28, x=1943),
        _line_at("￥1057.43", 921, 29, x=1377),
        _line_at("￥10.57", 922, 30, x=1920),
        _line_at("合", 924, 31, x=204),
        _line_at("计", 924, 32, x=357),
        _line_at("壹仟零陆拾捌圆整", 982, 33, x=621),
        _line_at("（小写）¥1068.00", 983, 34, x=1427, height=44),
        _line_at("价税合计 （大写)", 988, 35, x=168),
    ]

    result = extract_fields(lines, (2067, 1378), "vat_digital")

    assert result.fields["total_amount"].value == "1068.00"
    assert result.fields["amount"].value == "1057.43"      # 不是被"价税合计"抢走的 1068
    assert result.fields["tax_amount"].value == "10.57"
    assert result.fields["tax_amount"].rule_id.startswith("paired")


def test_tax_no_corrects_digit_letter_confusion() -> None:
    from app.ocr.extractor import _normalize

    # OCR 把 0 读成 O：O 不在 USCC 字符集内，映射回 0 是安全的
    assert _normalize("tax_no", "9113O73OMAK5MAN6OW") == "91130730MAK5MAN60W"


def test_code_corrects_digit_letter_confusion() -> None:
    from app.ocr.extractor import _normalize

    assert _normalize("code", "2613O0000034367O3626") == "26130000003436703626"


def test_tax_no_field_value_uses_corrected_text() -> None:
    """display 用纠正值，raw_text 保留机器原样（审计能看到 O）。"""
    lines = [_line("统一社会信用代码/纳税人识别号：9113O73OMAK5MAN6OW", 300, 0)]
    result = extract_fields(lines, (1200, 800), "vat_digital")
    field = result.fields["seller_tax_no"]
    assert field.value == field.raw_value == "91130730MAK5MAN60W"
    assert "O" in field.raw_text
