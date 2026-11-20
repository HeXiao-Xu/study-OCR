"""字段级/整单置信度聚合（S3-2）。

为什么单独一个模块：置信度是"要不要人工看"的唯一判据，公式与阈值必须可配置、可单测、
**只有一个实现**。抽取器只负责把证据（锚点行 score / 值行 score / 取值一致性 / 规则强度）
带出来，不自己决定三档；流水线只负责聚合与落库；接口只派生展示用的 `review_level`。

公式（D-058）：
    ocr_base = w_anchor × anchor_score + w_value × value_score     # 默认 0.35 / 0.65
    raw      = ocr_base × agreement × rule_factor                  # 三者都在 [0,1]
    score    = round(clamp(raw, 0, 1), 3)
    validators 判错 → min(score, validator_cap=0.45)                # 任务卡"直接 <0.5"
    level    = auto (>=0.90) / review (>=0.60) / manual
    冲突字段（同优先级多候选）不允许 auto，降为 review —— 候选必须由人挑，不能机器替人做决定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.core.config import Settings, get_settings

__all__ = [
    "AUTO",
    "MANUAL",
    "REVIEW",
    "FieldConfidence",
    "FieldScore",
    "InvoiceConfidence",
    "ReviewLevel",
    "aggregate_invoice",
    "apply_validator_cap",
    "compute_field_confidence",
    "level_for",
    "resolve_field",
]

ReviewLevel = Literal["auto", "review", "manual"]
AUTO: ReviewLevel = "auto"
REVIEW: ReviewLevel = "review"
MANUAL: ReviewLevel = "manual"


def _settings(settings: Settings | None) -> Settings:
    return settings if settings is not None else get_settings()


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


@dataclass(frozen=True)
class FieldScore:
    """输入：一个字段的原始证据（抽取器/推导值给出）。"""

    field_name: str
    score: float
    conflict: bool = False
    validator_failed: bool = False


@dataclass(frozen=True)
class FieldConfidence:
    """输出：落库与前端使用的最终分数与档位。"""

    field_name: str
    score: float
    level: ReviewLevel


@dataclass(frozen=True)
class InvoiceConfidence:
    """整单聚合结果（`overall` 只用于展示/排序，不参与单字段判定）。"""

    overall: float
    needs_review: list[str] = field(default_factory=list)
    levels: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {"auto": 0, "review": 0, "manual": 0})


def compute_field_confidence(
    anchor_score: float,
    value_score: float,
    *,
    agreement: float = 1.0,
    rule_factor: float = 1.0,
    settings: Settings | None = None,
) -> float:
    """锚点/值行分数加权后，乘取值一致性与规则强度。"""
    conf = _settings(settings)
    base = (
        conf.confidence_weight_anchor * float(anchor_score)
        + conf.confidence_weight_value * float(value_score)
    )
    return round(_clamp(base * float(agreement) * float(rule_factor)), 3)


def apply_validator_cap(score: float, *, settings: Settings | None = None) -> float:
    """校验器判错的字段封顶：不是"再打折"，而是直接压到"必须人工"区（任务卡要求 <0.5）。"""
    cap = _settings(settings).confidence_validator_cap
    return round(min(float(score), cap), 3)


def level_for(score: float, *, settings: Settings | None = None) -> ReviewLevel:
    conf = _settings(settings)
    if score >= conf.confidence_auto_confirm:
        return AUTO
    if score >= conf.confidence_manual_below:
        return REVIEW
    return MANUAL


def resolve_field(item: FieldScore, *, settings: Settings | None = None) -> FieldConfidence:
    """封顶 → 分档 → 冲突降级（顺序固定，测试与本函数是唯一口径）。"""
    score = (
        apply_validator_cap(item.score, settings=settings)
        if item.validator_failed
        else round(float(item.score), 3)
    )
    level = level_for(score, settings=settings)
    if item.conflict and level == AUTO:
        level = REVIEW
    return FieldConfidence(field_name=item.field_name, score=score, level=level)


def aggregate_invoice(items: list[FieldConfidence]) -> InvoiceConfidence:
    if not items:
        return InvoiceConfidence(overall=0.0)

    counts = {"auto": 0, "review": 0, "manual": 0}
    for item in items:
        counts[item.level] += 1
    overall = round(sum(item.score for item in items) / len(items), 3)
    return InvoiceConfidence(
        overall=overall,
        needs_review=sorted(item.field_name for item in items if item.level != AUTO),
        levels={item.field_name: item.level for item in items},
        counts=counts,
    )