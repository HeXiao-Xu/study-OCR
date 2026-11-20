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

# --------------------------------------------------------------------------- #
# S3-4：导出物与前端类型的防漂移
# --------------------------------------------------------------------------- #
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

MODELS = {
    "OcrLine": OcrLine,
    "OcrResult": OcrResult,
    "ExtractedField": ExtractedField,
    "FieldError": FieldError,
    "ExtractionResult": ExtractionResult,
}
ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "schemas" / "ocr_result.schema.json"
TS_PATH = ROOT / "web" / "src" / "types" / "ocr.ts"


def test_committed_json_schema_matches_models() -> None:
    from export_ocr_schema import build_schema

    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == build_schema(), "docs/schemas/ocr_result.schema.json 过期，请重跑导出脚本"


def _ts_interface_fields(name: str) -> set[str]:
    text = TS_PATH.read_text(encoding="utf-8")
    body = re.search(rf"export interface {name} \{{(.*?)\n\}}", text, re.S)
    assert body is not None, f"types/ocr.ts 缺少 interface {name}"
    return set(re.findall(r"^\s{2}([a-z_][a-z0-9_]*)\??:", body.group(1), re.M))


def test_frontend_types_match_contract() -> None:
    for name, model in MODELS.items():
        assert _ts_interface_fields(name) == set(model.model_fields), (
            f"web/src/types/ocr.ts 的 {name} 与后端契约不一致："
            f"TS={sorted(_ts_interface_fields(name))} model={sorted(model.model_fields)}"
        )


def test_template_field_names_are_in_whitelist() -> None:
    from app.services.invoice_service import INVOICE_FIELD_NAMES
    from app.services.template_seed import build_seed_templates

    names = {name for template in build_seed_templates() for name in template.field_specs_json}
    assert names, "seed 模板没有字段规格"
    assert names <= set(INVOICE_FIELD_NAMES), (
        f"模板里出现白名单外字段：{sorted(names - set(INVOICE_FIELD_NAMES))}"
    )
