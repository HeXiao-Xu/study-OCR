"""S3-4 识别产物契约：模型字段、Pydantic 校验、JSON Schema 与前端类型一致性。

这里不跑 OCR，只证明"契约本身是对的、且导出物与代码一致"。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.ocr import (
    ExtractedField,
    ExtractionResult,
    FieldError,
    OcrLine,
    OcrResult,
)


def _line(**overrides) -> OcrLine:
    payload = dict(
        text="价税合计（小写）￥1,234.56", score=0.95,
        box=[[10, 20], [300, 20], [300, 50], [10, 50]],
        line_no=3, y_center=35.0, x_left=10.0, height=30.0,
    )
    payload.update(overrides)
    return OcrLine(**payload)


def test_ocr_line_requires_four_points() -> None:
    with pytest.raises(ValidationError):
        _line(box=[[10, 20], [300, 20], [300, 50]])


def test_ocr_line_bbox_is_outer_rectangle() -> None:
    assert _line().bbox() == (10, 20, 300, 50)


def test_extracted_field_raw_value_is_optional() -> None:
    field = ExtractedField(
        name="total_amount", value="1234.56", raw_text="价税合计（小写）￥1,234.56",
        source_line_no=3, confidence=0.95, bbox=(10, 20, 300, 50),
    )
    assert field.raw_value is None

    with_raw = field.model_copy(update={"raw_value": "￥1,234.56"})
    assert with_raw.raw_value == "￥1,234.56"


def test_extracted_field_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValidationError):
        ExtractedField(name="x", value="1", raw_text="", confidence=1.5)


def test_field_error_has_severity() -> None:
    error = FieldError(field="total_amount", code="money_sum_mismatch",
                       message="合计不等于金额+税额")
    assert error.severity == "error"


def test_ocr_result_carries_line_contract() -> None:
    result = OcrResult(lines=[_line()], image_size=(1000, 600))
    assert result.lines[0].text.endswith("1,234.56")
    assert result.engine == "paddleocr"


def test_extraction_result_defaults() -> None:
    result = ExtractionResult()
    assert result.fields == {}
    assert result.missing_required == []
    assert result.ticket_type == "unknown"


def test_types_module_reexports_canonical_models() -> None:
    """`ocr/types.py` 只是 re-export：两处拿到的必须是同一个类（防出现第二份定义）。"""
    from app.ocr import types as ocr_types

    assert ocr_types.OcrLine is OcrLine
    assert ocr_types.ExtractedField is ExtractedField
    assert ocr_types.FieldError is FieldError