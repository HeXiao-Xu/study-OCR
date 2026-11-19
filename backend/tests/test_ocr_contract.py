"""S1-3 OCR 输出契约测试。

为什么单独一个文件：`paddleocr` 2.7 与 3.x 的返回结构**完全不兼容**
（`[[[box,(text,score)]]]` vs `predict()` 的 `rec_texts/rec_scores/dt_polys`）。
把解析与排序逻辑固定成契约测试，库升级时**测试先炸**，而不是让上层拿到空列表
还以为"这张图上没字"——那种 bug 最难查。

这里不加载真实模型（不 mock 到 PaddleOCR 内部），只喂"2.7 形态的假数据"。
真实推理另有 `@pytest.mark.slow` 的用例。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.ocr.detector import OcrContractError, OcrEngine, OcrOptions, parse_paddle_result
from app.ocr.recognizer import recognize_line


def _line(box, text, score):
    return [box, (text, score)]


def test_parse_returns_sorted_lines_with_geometry() -> None:
    raw = [[
        _line([[10, 60], [190, 60], [190, 84], [10, 84]], "第二行", 0.91),
        _line([[10, 10], [190, 10], [190, 34], [10, 34]], "第一行", 0.99),
        _line([[200, 10], [380, 10], [380, 34], [200, 34]], "第一行右侧", 0.88),
    ]]
    result = parse_paddle_result(raw, (400, 100))

    assert [line.text for line in result.lines] == ["第一行", "第一行右侧", "第二行"]
    assert [line.line_no for line in result.lines] == [0, 1, 2]
    assert result.lines[0].bbox() == (10, 10, 190, 34)
    assert result.lines[0].y_center == pytest.approx(22.0)
    assert result.lines[0].height == pytest.approx(24.0)
    assert result.mean_score == pytest.approx((0.99 + 0.88 + 0.91) / 3)


def test_parse_is_deterministic() -> None:
    """同一份输入必须得到完全相同的行序与行号——S1-5 靠 line_no 回查。"""
    raw = [[
        _line([[10, 12], [90, 12], [90, 40], [10, 40]], "A", 0.9),
        _line([[10, 14], [90, 14], [90, 42], [10, 42]], "B", 0.9),
        _line([[10, 50], [90, 50], [90, 78], [10, 78]], "C", 0.9),
    ]]
    first = parse_paddle_result(raw, (100, 100))
    second = parse_paddle_result(raw, (100, 100))
    assert [(l.text, l.line_no) for l in first.lines] == [(l.text, l.line_no) for l in second.lines]


def test_empty_pages_return_empty_list_without_raising() -> None:
    for raw in ([], [None], [[]]):
        result = parse_paddle_result(raw, (100, 100))
        assert result.lines == []
        assert result.is_empty is True


def test_v3_dict_shape_raises_contract_error() -> None:
    """3.x 的 dict 结构必须报明确错误，不能静默当空结果。"""
    v3_like = [{"rec_texts": ["发票"], "rec_scores": [0.9], "dt_polys": []}]
    with pytest.raises(OcrContractError) as excinfo:
        parse_paddle_result(v3_like, (100, 100))
    assert "3.x" in str(excinfo.value)


def test_malformed_line_raises_contract_error() -> None:
    with pytest.raises(OcrContractError):
        parse_paddle_result([["不是两点组"]], (100, 100))


def test_engine_singleton_returns_same_instance() -> None:
    from app.ocr.detector import get_engine, reset_engine

    reset_engine()
    assert get_engine() is get_engine()
    reset_engine()


def test_recognize_line_degrades_on_empty_crop() -> None:
    """空裁剪不能抛异常——S3-3 的二次识别可能裁到空白。"""
    empty = np.zeros((0, 0, 3), dtype=np.uint8)
    assert recognize_line(empty) == ("", 0.0)

    engine = OcrEngine(OcrOptions())
    assert engine.recognize_line(empty) == ("", 0.0)