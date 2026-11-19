"""S1-4 票种识别与区域划分的单元测试（不加载模型）。

票种判错的代价：S1-5 会用错模板、字段抽取整页失败。所以这里锁两件事：
① 五类票种必须能判对；② 判不准时必须退成 unknown，而不是硬猜一个。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.ocr.layout import (
    DEFAULT_TEMPLATE_THRESHOLD,
    analyze_layout,
    assign_regions,
    classify_ticket,
    load_templates,
)
from app.ocr.types import OcrLine


def _line(text: str, y: float, *, height: float = 24.0, line_no: int = 0, x_left: float = 10.0) -> OcrLine:
    return OcrLine(
        text=text, score=0.99,
        box=[[x_left, y - height / 2], [x_left + 300, y - height / 2],
             [x_left + 300, y + height / 2], [x_left, y + height / 2]],
        line_no=line_no, y_center=y, x_left=x_left, height=height,
    )


def _lines(*items: tuple[str, float]) -> list[OcrLine]:
    return [_line(text, y, line_no=index) for index, (text, y) in enumerate(items)]


SIZE_LANDSCAPE = (1240, 827)


def test_templates_load_all_five_types() -> None:
    templates = load_templates()
    assert set(templates) == {"vat_digital", "vat_general", "train_ticket", "flight_itinerary", "taxi_ticket"}
    for template in templates.values():
        assert template.keywords and template.regions
        assert template.max_score > 0


@pytest.mark.parametrize(
    ("lines", "size", "expected"),
    [
        # 数电票：标题含"电子发票（普通发票）"，且**没有**发票代码/校验码
        (_lines(("电子发票（普通发票）", 48), ("发票号码：2613", 80), ("开票日期：2025年11月04日", 120),
                ("名称：某公司", 240), ("价税合计（大写）", 580)), SIZE_LANDSCAPE, "vat_digital"),
        # 旧版增值税电子普通发票：有"增值税""校验码"
        (_lines(("增值税电子普通发票", 40), ("发票代码：042001900111", 200), ("校验码：12345678901234567890", 740)),
         (1200, 800), "vat_general"),
        # 火车票
        (_lines(("铁路电子客票", 50), ("车次：G1234", 260), ("乘车人：张伟", 380), ("票价：¥553.00", 460)),
         (600, 1000), "train_ticket"),
        # 行程单
        (_lines(("航空运输电子客票行程单", 50), ("航班号：MU1234", 230), ("民航发展基金", 430)),
         (1200, 700), "flight_itinerary"),
        # 出租车定额票
        (_lines(("武汉市出租汽车定额发票", 18), ("车牌号：鄂A12345", 168), ("金额：¥36.00", 200)),
         (480, 300), "taxi_ticket"),
    ],
)
def test_classify_each_ticket_type(lines: list[OcrLine], size: tuple[int, int], expected: str) -> None:
    result = classify_ticket(lines, size)
    assert result.ticket_type == expected
    assert result.score >= DEFAULT_TEMPLATE_THRESHOLD


def test_blank_lines_return_unknown_without_raising() -> None:
    result = classify_ticket([], SIZE_LANDSCAPE)
    assert result.ticket_type == "unknown"
    assert result.is_known is False


def test_unrelated_text_returns_unknown() -> None:
    lines = _lines(("今天天气不错", 100), ("随便写点东西", 140))
    result = classify_ticket(lines, SIZE_LANDSCAPE)
    assert result.ticket_type == "unknown"
    assert result.warnings


def test_ambiguous_scores_return_unknown_rather_than_guessing() -> None:
    """两类模板打分打平（margin 不足）时必须 unknown，而不是靠"先遍历到谁"决定。

    构造两个关键字完全相同、归一化分母也相同的模板 —— 这是"真歧义"的形态。
    （若两者标题关键字不同，标题区权重会让强者胜出，那是正常判定而不是歧义。）
    """
    from app.ocr.layout import TicketTemplate

    keywords = [{"text": "发票", "weight": 5}]
    regions = {"header": [0.0, 0.0, 1.0, 0.3], "body": [0.0, 0.3, 1.0, 1.0], "footer": [0.0, 1.0, 1.0, 1.0]}
    twins = {
        "type_a": TicketTemplate("type_a", "A 类", keywords, [], regions, None, (0.5, 2.0)),
        "type_b": TicketTemplate("type_b", "B 类", keywords, [], regions, None, (0.5, 2.0)),
    }
    lines = _lines(("这是一张发票", 40))
    result = classify_ticket(lines, (1200, 800), twins)
    assert result.ticket_type == "unknown"
    assert any(w.startswith("ambiguous") for w in result.warnings)


def test_regions_assign_every_line_and_are_monotonic() -> None:
    lines = _lines(("电子发票（普通发票）", 40), ("发票号码：2613", 80), ("名称：某公司", 250),
                   ("项目名称", 330), ("合计", 560), ("开票人：陈晨", 790))
    result = analyze_layout(lines, SIZE_LANDSCAPE)
    assignment = result.regions

    # 每行都必须有归属（不能丢行）
    assert set(assignment.line_regions) == {line.line_no for line in lines}
    assert set(assignment.line_regions.values()) <= {"header", "body", "footer"}
    # 标题在上、开票人在下：区域顺序必须单调（header 的行 y 必须小于 footer 的行 y）
    by_region: dict[str, list[float]] = {}
    for line in lines:
        by_region.setdefault(assignment.line_regions[line.line_no], []).append(line.y_center)
    if "header" in by_region and "footer" in by_region:
        assert max(by_region["header"]) < min(by_region["footer"])


def test_unknown_ticket_puts_all_lines_in_body() -> None:
    lines = _lines(("完全无关的文字一", 100), ("完全无关的文字二", 140))
    assignment = assign_regions(lines, SIZE_LANDSCAPE)
    assert set(assignment.line_regions.values()) == {"body"}
    assert any("unknown_ticket" in warning for warning in assignment.warnings)


def test_blocks_group_adjacent_lines() -> None:
    lines = _lines(("第一行", 100), ("第二行", 126), ("第三行", 400))
    assignment = assign_regions(lines, SIZE_LANDSCAPE)
    block_line_counts = sorted(len(block.line_nos) for block in assignment.blocks)
    assert block_line_counts[-1] >= 2      # 相邻两行应合成一块
    assert sum(len(block.line_nos) for block in assignment.blocks) == len(lines)


def test_regions_use_normalized_coordinates() -> None:
    """同一张图放大 2 倍，区域归属不应改变（归一化坐标的意义）。"""
    lines_small = _lines(("电子发票（普通发票）", 40), ("合计", 560))
    lines_big = [_line(line.text, line.y_center * 2, height=line.height * 2,
                       line_no=line.line_no, x_left=line.x_left * 2) for line in lines_small]
    small = analyze_layout(lines_small, SIZE_LANDSCAPE)
    big = analyze_layout(lines_big, (SIZE_LANDSCAPE[0] * 2, SIZE_LANDSCAPE[1] * 2))
    assert small.regions.line_regions == big.regions.line_regions