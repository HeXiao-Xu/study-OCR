"""S3-2 置信度聚合：公式、封顶、三档、整单聚合。

阈值与权重全部通过显式 `Settings` 传入，测试不读 `.env`（本地/CI 结果一致）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.confidence import (
    FieldConfidence,
    FieldScore,
    aggregate_invoice,
    apply_validator_cap,
    compute_field_confidence,
    level_for,
    resolve_field,
)

_BASE = dict(
    database_url="postgresql://u:p@127.0.0.1:5433/db",
    redis_url="redis://:p@127.0.0.1:6380/0",
    minio_endpoint="127.0.0.1:9002",
    minio_access_key="ak",
    minio_secret_key="sk",
    minio_bucket="bucket",
    jwt_secret="s" * 32,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_BASE, **overrides})


def test_compute_field_confidence_is_weighted_base_times_factors() -> None:
    settings = _settings()
    # 0.35*0.8 + 0.65*0.9 = 0.865
    assert compute_field_confidence(0.8, 0.9, settings=settings) == pytest.approx(0.865)
    # × agreement 0.85 × rule_factor 0.84
    assert compute_field_confidence(
        0.8, 0.9, agreement=0.85, rule_factor=0.84, settings=settings
    ) == pytest.approx(0.618)      # 0.865×0.85×0.84 = 0.61761 → round 3


def test_compute_field_confidence_clamps_to_unit_interval() -> None:
    settings = _settings(confidence_weight_anchor=1.0, confidence_weight_value=0.0)
    assert compute_field_confidence(1.0, 1.0, agreement=1.2, settings=settings) == 1.0
    assert compute_field_confidence(0.0, 0.0, settings=settings) == 0.0


def test_level_for_boundaries() -> None:
    settings = _settings()
    assert level_for(0.90, settings=settings) == "auto"
    assert level_for(0.899, settings=settings) == "review"
    assert level_for(0.60, settings=settings) == "review"
    assert level_for(0.599, settings=settings) == "manual"


def test_validator_cap_pins_failed_field_below_half() -> None:
    settings = _settings()
    assert apply_validator_cap(0.92, settings=settings) == 0.45
    assert apply_validator_cap(0.30, settings=settings) == 0.30      # 不反弹


def test_resolve_field_forces_conflict_into_review() -> None:
    settings = _settings()
    resolved = resolve_field(FieldScore("total_amount", 0.97, conflict=True), settings=settings)
    assert resolved.level == "review"
    assert resolved.score == 0.97

    failed = resolve_field(
        FieldScore("total_amount", 0.97, validator_failed=True), settings=settings
    )
    assert failed.score == 0.45
    assert failed.level == "manual"


def test_aggregate_invoice_mean_and_levels() -> None:
    items = [
        FieldConfidence("invoice_no", 0.97, "auto"),
        FieldConfidence("total_amount", 0.75, "review"),
        FieldConfidence("buyer_name", 0.40, "manual"),
    ]

    result = aggregate_invoice(items)

    assert result.overall == pytest.approx(0.707)          # (0.97+0.75+0.40)/3
    assert result.needs_review == ["buyer_name", "total_amount"]   # 按字段名排序
    assert result.counts == {"auto": 1, "review": 1, "manual": 1}
    assert result.levels["buyer_name"] == "manual"


def test_aggregate_invoice_empty_is_zero() -> None:
    result = aggregate_invoice([])
    assert result.overall == 0.0
    assert result.needs_review == []
    assert result.counts == {"auto": 0, "review": 0, "manual": 0}


@pytest.mark.parametrize(
    "overrides",
    [
        {"confidence_weight_anchor": 0.5, "confidence_weight_value": 0.2},   # 和 ≠ 1
        {"confidence_weight_anchor": -0.1, "confidence_weight_value": 1.1},  # 越界
        {"confidence_auto_confirm": 0.5},                                     # auto <= manual
        {"confidence_validator_cap": 0.8},                                    # 封顶 >= manual
    ],
)
def test_confidence_settings_are_validated(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)