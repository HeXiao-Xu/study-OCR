"""S1-6 指标函数单测：全部用手算小样本把数值钉死。

指标口径漂移 = 论文数字不可复现。这里每个期望值都是手算的，不是"跑一遍看看"。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from eval.metrics import (
    Box,
    char_accuracy,
    document_accuracy,
    edit_distance,
    field_accuracy,
    field_correct,
    iou,
    match_detections,
    need_review_metrics,
    percentile,
    precision_recall_hmean,
)


# ---------------------------------------------------------------------------
# IoU
# ---------------------------------------------------------------------------
def test_iou_identical_boxes() -> None:
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == pytest.approx(1.0)


def test_iou_disjoint_boxes() -> None:
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0


def test_iou_hand_computed() -> None:
    # 重叠 5×10 = 50；并集 100 + 100 − 50 = 150 → 1/3
    assert iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(50 / 150, abs=1e-9)


def test_iou_accepts_four_points() -> None:
    quad = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert iou(quad, [0, 0, 10, 10]) == pytest.approx(1.0)


def test_iou_degenerate_box_returns_zero() -> None:
    assert iou([0, 0, 0, 0], [0, 0, 10, 10]) == 0.0


# ---------------------------------------------------------------------------
# 检测匹配
# ---------------------------------------------------------------------------
def test_match_detections_perfect() -> None:
    gt = [(0, 0, 10, 10), (20, 20, 30, 30)]
    pred = [((0, 0, 10, 10), 0.9), ((20, 20, 30, 30), 0.8)]
    score = match_detections(pred, gt)
    assert (score.tp, score.fp, score.fn) == (2, 0, 0)
    assert score.precision == 1.0 and score.recall == 1.0 and score.hmean == 1.0


def test_match_detections_counts_fp_and_fn() -> None:
    gt = [(0, 0, 10, 10)]
    pred = [((100, 100, 110, 110), 0.9)]      # 位置完全错
    score = match_detections(pred, gt)
    assert (score.tp, score.fp, score.fn) == (0, 1, 1)
    assert score.hmean == 0.0


def test_match_detections_one_to_one() -> None:
    """两个预测框都盯着同一个标注：只能有一个算 TP，另一个必须算 FP。"""
    gt = [(0, 0, 10, 10)]
    pred = [((0, 0, 10, 10), 0.9), ((0, 0, 10, 10), 0.8)]
    score = match_detections(pred, gt)
    assert (score.tp, score.fp, score.fn) == (1, 1, 0)


def test_match_detections_respects_iou_threshold() -> None:
    gt = [(0, 0, 10, 10)]
    pred = [((5, 0, 15, 10), 0.9)]            # IoU = 1/3
    assert match_detections(pred, gt, iou_threshold=0.5).tp == 0
    assert match_detections(pred, gt, iou_threshold=0.3).tp == 1


# ---------------------------------------------------------------------------
# 字符准确率
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("pred", "exp", "distance"), [
    ("abc", "abc", 0),
    ("abc", "abd", 1),
    ("abc", "", 3),
    ("", "abc", 3),
    ("kitten", "sitting", 3),
])
def test_edit_distance_hand_computed(pred: str, exp: str, distance: int) -> None:
    assert edit_distance(pred, exp) == distance


def test_char_accuracy_hand_computed() -> None:
    # 标注 4 字，错 1 字 → 0.75
    assert char_accuracy("发票代码", "发票代码") == 1.0
    assert char_accuracy("发票代玛", "发票代码") == pytest.approx(0.75)


def test_char_accuracy_empty_annotation_is_zero() -> None:
    assert char_accuracy("随便", "") == 0.0


# ---------------------------------------------------------------------------
# 字段与整单
# ---------------------------------------------------------------------------
def test_field_correct_money_uses_decimal() -> None:
    assert field_correct("total_amount", "922.75", "922.75")
    assert field_correct("total_amount", "922.75", "922.750")     # Decimal 归一
    # 容差 0.01 是"含"的：差 0.01 仍算对，差 0.02 才算错
    assert field_correct("total_amount", "922.75", "922.76")
    assert not field_correct("total_amount", "922.75", "922.77")
    assert field_correct("total_amount", "922.75", "922.77", money_tolerance=Decimal("0.02"))


def test_field_correct_text_normalizes_spaces_and_case() -> None:
    assert field_correct("buyer_tax_no", "9142bbuja57h9nhhcw", "9142BBUJA57H9NHHCW")
    assert field_correct("invoice_date", "2025-11-04", "2025-11-04")
    assert not field_correct("buyer_name", "武汉金穗商贸有限公司", "武汉金穗商贸")


def test_field_correct_missing_is_wrong() -> None:
    assert not field_correct("invoice_no", "12345678", None)
    assert not field_correct("invoice_no", None, "12345678")


def test_field_accuracy_denominator_is_annotated_count() -> None:
    rows = [
        {"field": "invoice_no", "correct": True},
        {"field": "invoice_no", "correct": False},
        {"field": "total_amount", "correct": True},
    ]
    stats = field_accuracy(rows)
    assert stats["invoice_no"] == {"count": 2, "correct": 1, "accuracy": 0.5}
    assert stats["total_amount"] == {"count": 1, "correct": 1, "accuracy": 1.0}


def test_document_accuracy_all_fields_required() -> None:
    rows = [
        {"image": "a", "required_correct": [True, True]},
        {"image": "b", "required_correct": [True, False]},
        {"image": "c", "required_correct": [False]},
        {"image": "d", "required_correct": []},        # 没有必填字段 → 不计入分母
    ]
    assert document_accuracy(rows) == pytest.approx(1 / 3)


def test_need_review_metrics_hand_computed() -> None:
    reviewed = [
        {"confidence": 0.9, "correct": True},
        {"confidence": 0.4, "correct": False},
        {"confidence": 0.3, "correct": True},
        {"confidence": 0.95, "correct": False},
    ]
    metrics = need_review_metrics(reviewed, confidence_threshold=0.5)
    assert metrics["need_review_rate"] == pytest.approx(0.5)      # 2/4
    assert metrics["low_conf_error_rate"] == pytest.approx(0.5)   # 1/2


def test_percentile_linear_interpolation() -> None:
    values = [1, 2, 3, 4]
    assert percentile(values, 0.5) == pytest.approx(2.5)
    assert percentile(values, 0.0) == 1.0
    assert percentile(values, 1.0) == 4.0
    assert percentile([], 0.5) == 0.0