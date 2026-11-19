"""评测指标（S1-6）。

口径以 `docs/EVAL.md` §2 为准，**这一个文件是唯一实现**：脚本只负责跑数据、调这里的纯函数。
每个指标都是纯函数，`backend/tests/test_metrics.py` 用手算小样本把数值钉死——
口径一旦漂移，论文数字就不可复现。

为什么指标要独立于产框代码：如果"打分"和"产结果"是同一份逻辑，错在哪都看不出来（等于自证）。
这里的输入是"预测框/预测文本"与"标注框/标注文本"，两边完全解耦。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import mean

__all__ = [
    "Box", "DetectionScore", "iou", "match_detections", "coverage_match", "containment",
    "precision_recall_hmean",
    "edit_distance", "char_accuracy", "field_correct", "field_accuracy",
    "document_accuracy", "need_review_metrics", "percentile",
]

Box = tuple[float, float, float, float]          # [x1, y1, x2, y2]
Point = list[float]


# ---------------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------------
def _to_xyxy(box) -> Box:
    """接受 [x1,y1,x2,y2] 或四点 [[x,y]×4]，统一成外接矩形。"""
    if box and isinstance(box[0], (list, tuple)):
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        return min(xs), min(ys), max(xs), max(ys)
    x1, y1, x2, y2 = (float(value) for value in box[:4])
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def iou(box_a, box_b) -> float:
    """交并比。任一框面积为 0 时返回 0（不要返回 NaN 污染下游）。"""
    ax1, ay1, ax2, ay2 = _to_xyxy(box_a)
    bx1, by1, bx2, by2 = _to_xyxy(box_b)
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return 0.0 if union <= 0 else intersection / union


@dataclass
class DetectionScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    hmean: float = 0.0
    matched_ious: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"tp": self.tp, "fp": self.fp, "fn": self.fn,
                "precision": round(self.precision, 4), "recall": round(self.recall, 4),
                "hmean": round(self.hmean, 4),
                "mean_matched_iou": round(mean(self.matched_ious), 4) if self.matched_ious else 0.0}


def match_detections(pred_boxes: list[tuple[Box, float]], gt_boxes: list[Box],
                     iou_threshold: float = 0.5) -> DetectionScore:
    """一对一贪婪匹配：预测按置信度降序，每个预测只能配一个还没被占用的标注。"""
    score = DetectionScore()
    used = [False] * len(gt_boxes)
    for box, _confidence in sorted(pred_boxes, key=lambda item: item[1], reverse=True):
        best_index, best_iou = -1, 0.0
        for index, gt_box in enumerate(gt_boxes):
            if used[index]:
                continue
            overlap = iou(box, gt_box)
            if overlap > best_iou:
                best_index, best_iou = index, overlap
        if best_index >= 0 and best_iou >= iou_threshold:
            used[best_index] = True
            score.tp += 1
            score.matched_ious.append(best_iou)
        else:
            score.fp += 1
    score.fn = used.count(False)
    _fill_pr(score)
    return score


def containment(inner, outer) -> float:
    """`inner` 有多少面积落在 `outer` 里（0–1）。"""
    ix1, iy1, ix2, iy2 = _to_xyxy(inner)
    ox1, oy1, ox2, oy2 = _to_xyxy(outer)
    inter_w = max(0.0, min(ix2, ox2) - max(ix1, ox1))
    inter_h = max(0.0, min(iy2, oy2) - max(iy1, oy1))
    area = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    return 0.0 if area <= 0 else (inter_w * inter_h) / area


def coverage_match(pred_boxes: list[tuple[Box, float]], gt_boxes: list[Box],
                   iou_threshold: float = 0.5, containment_threshold: float = 0.85) -> DetectionScore:
    """检测匹配（适配"元素级标注 vs 行级输出"）。

    纯 IoU 匹配在这里会系统性偏低：标注把一行的若干单元格记成多个框，而引擎输出 1 行，
    结果是 1 TP + 若干 FN（实测合并标注后 Hmean 只有 0.12）。这里改用"覆盖"语义并**双向**判定：

    - **召回**：标注框被某个预测框 IoU≥阈值**或** 85% 面积被它包住，即算命中；
    - **精确**：预测框 IoU≥阈值**或** 至少把一个标注框的 50% 包住，才算有效（不是凭空多框）。

    这样"引擎把同一行的多个元素合成一行"既不会被判漏检，也不会被算成误检。
    """
    score = DetectionScore()
    if not gt_boxes:
        score.fp = len(pred_boxes)
        _fill_pr(score)
        return score
    if not pred_boxes:
        score.fn = len(gt_boxes)
        _fill_pr(score)
        return score

    for gt_box in gt_boxes:
        best = 0.0
        for pred_box, _confidence in pred_boxes:
            overlap = iou(pred_box, gt_box)
            if overlap >= iou_threshold or containment(gt_box, pred_box) >= containment_threshold:
                best = max(best, overlap)
        if best > 0:
            score.tp += 1
            score.matched_ious.append(best)
    score.fn = len(gt_boxes) - score.tp

    for pred_box, _confidence in pred_boxes:
        covered = any(
            iou(pred_box, gt_box) >= iou_threshold or containment(gt_box, pred_box) >= 0.5
            for gt_box in gt_boxes
        )
        if not covered:
            score.fp += 1

    _fill_pr(score)
    return score


def precision_recall_hmean(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    hmean = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, hmean


def _fill_pr(score: DetectionScore) -> None:
    score.precision, score.recall, score.hmean = precision_recall_hmean(score.tp, score.fp, score.fn)


# ---------------------------------------------------------------------------
# 字符准确率
# ---------------------------------------------------------------------------
def edit_distance(predicted: str, expected: str) -> int:
    """Levenshtein 距离（滚动数组，O(min(n,m)) 空间）。"""
    if predicted == expected:
        return 0
    if not predicted:
        return len(expected)
    if not expected:
        return len(predicted)
    if len(predicted) < len(expected):
        predicted, expected = expected, predicted
    previous = list(range(len(expected) + 1))
    for i, char_p in enumerate(predicted, start=1):
        current = [i]
        for j, char_e in enumerate(expected, start=1):
            cost = 0 if char_p == char_e else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def char_accuracy(predicted: str, expected: str) -> float:
    """`1 − 编辑距离/标注长度`。标注为空时返回 0（没有可比内容，不计入平均）。"""
    expected = expected or ""
    predicted = predicted or ""
    if not expected:
        return 0.0
    return max(0.0, 1.0 - edit_distance(predicted, expected) / len(expected))


# ---------------------------------------------------------------------------
# 字段 / 整单
# ---------------------------------------------------------------------------
def _as_decimal(value: str | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value).replace(",", ""))
    except Exception:  # noqa: BLE001 - 非数值字段走字符串比较
        return None


_MONEY_FIELDS = {"amount", "tax_amount", "total_amount", "total_amount_upper"}


def field_correct(name: str, expected: str | None, actual: str | None,
                  money_tolerance: Decimal = Decimal("0.01")) -> bool:
    """单字段是否完全正确。金额用 Decimal 比较（带 0.01 容差），其余去空格后精确比较。"""
    if expected is None or actual is None:
        return False
    if name in _MONEY_FIELDS:
        left, right = _as_decimal(expected), _as_decimal(actual)
        if left is None or right is None:
            return str(expected).strip() == str(actual).strip()
        return abs(left - right) <= money_tolerance
    left = str(expected).replace(" ", "").strip().upper()
    right = str(actual).replace(" ", "").strip().upper()
    return left == right


def field_accuracy(rows: list[dict]) -> dict[str, dict]:
    """`rows`: [{field, correct}] → 每字段的 count/correct/accuracy（分母=有标注的条数）。"""
    stats: dict[str, dict[str, int]] = {}
    for row in rows:
        name = row["field"]
        bucket = stats.setdefault(name, {"count": 0, "correct": 0})
        bucket["count"] += 1
        bucket["correct"] += 1 if row["correct"] else 0
    result = {}
    for name, bucket in stats.items():
        result[name] = {
            "count": bucket["count"],
            "correct": bucket["correct"],
            "accuracy": round(bucket["correct"] / bucket["count"], 4) if bucket["count"] else 0.0,
        }
    return result


def document_accuracy(rows: list[dict]) -> float:
    """整单正确率：一张票的**所有必填字段**都对才算正确。

    `rows`: [{image, required_correct: list[bool]}]；没有必填字段的票不计入分母。
    """
    counted = [row for row in rows if row["required_correct"]]
    if not counted:
        return 0.0
    return sum(1 for row in counted if all(row["required_correct"])) / len(counted)


def need_review_metrics(reviewed: list[dict], confidence_threshold: float) -> dict:
    """待确认率与"低置信命中率"。

    `reviewed`: [{confidence, correct}]。
    - 待确认率 = 置信度低于阈值的字段数 / 总字段数；
    - 低置信命中率 = 标为待确认且**确实错误**的字段数 / 标为待确认的字段数
      （这个数高说明阈值选得好：人工核对的都是错的；低则说明阈值太保守，白让人看）。
    """
    if not reviewed:
        return {"need_review_rate": 0.0, "low_conf_error_rate": 0.0, "reviewed": 0}
    flagged = [row for row in reviewed if row["confidence"] < confidence_threshold]
    errors = [row for row in flagged if not row["correct"]]
    return {
        "need_review_rate": round(len(flagged) / len(reviewed), 4),
        "low_conf_error_rate": round(len(errors) / len(flagged), 4) if flagged else 0.0,
        "reviewed": len(reviewed),
    }


def percentile(values: list[float], q: float) -> float:
    """线性插值分位数（不引 numpy，指标函数保持无依赖）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)