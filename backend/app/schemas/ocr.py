"""OCR 识别产物契约（S3-4 起**唯一来源**）。

谁在用：`ocr/detector.py`（产出 `OcrResult`）、`ocr/extractor.py`（产出 `ExtractionResult`）、
`services/ocr_pipeline.py`（落库）、`scripts/export_ocr_schema.py`（导出 JSON Schema）、
前端 `web/src/types/ocr.ts` 与评测脚本。

坐标口径（写死，S3-3/D-060）：
- `OcrLine.box` = **四点** `[[x,y]×4]`，顺时针，**校正图**坐标系；
- `ExtractedField.bbox` = `[x1,y1,x2,y2]` 外接框，**原图**坐标系（前端画框只认这一种）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "ExtractedField",
    "ExtractionResult",
    "FieldError",
    "OcrLine",
    "OcrResult",
]


class OcrLine(BaseModel):
    """一行文本：内容 + 置信度 + 四点坐标（**校正图**坐标系）。"""

    text: str
    score: float = Field(ge=0.0, le=1.0)
    box: list[list[float]] = Field(description="四点坐标 [[x,y]×4]，顺时针，校正图坐标系")
    line_no: int = Field(ge=0, description="排序后的行号：从上到下、从左到右")
    y_center: float = Field(description="四点框的 y 中心，用于版面分块")
    x_left: float = Field(description="四点框的最小 x，用于行内排序")
    height: float = Field(description="四点框的高度，用于字号估计与分桶")

    @field_validator("box")
    @classmethod
    def _check_box(cls, value: list[list[float]]) -> list[list[float]]:
        if len(value) != 4 or any(len(point) != 2 for point in value):
            raise ValueError("box 必须是 4 个 [x, y] 点")
        return value

    def bbox(self) -> tuple[int, int, int, int]:
        """四点 → 外接矩形 [x1,y1,x2,y2]（字段层用；坐标仍是校正图）。"""
        xs = [point[0] for point in self.box]
        ys = [point[1] for point in self.box]
        return int(round(min(xs))), int(round(min(ys))), int(round(max(xs))), int(round(max(ys)))


class OcrResult(BaseModel):
    """一次识别的完整结果。"""

    lines: list[OcrLine] = Field(default_factory=list)
    image_size: tuple[int, int] = Field(description="(宽, 高)，校正图尺寸")
    engine: str = "paddleocr"
    engine_version: str = "2.7.3"
    elapsed_ms: float = 0.0
    det_params: dict = Field(
        default_factory=dict, description="实际使用的检测参数，写进 ocr_jobs.params_json"
    )

    @property
    def is_empty(self) -> bool:
        return not self.lines

    @property
    def mean_score(self) -> float:
        return sum(line.score for line in self.lines) / len(self.lines) if self.lines else 0.0

    def find_by_keyword(self, keyword: str) -> list[OcrLine]:
        """按关键字筛行——S1-5 的"关键字锚定"用同一套筛选逻辑。"""
        return [line for line in self.lines if keyword in line.text]


class ExtractedField(BaseModel):
    """一个抽取出来的字段。保留来源行号、规则 id 与置信度成分，便于追溯与重算。"""

    name: str
    value: str = Field(description="归一值（金额 Decimal 字符串 / 日期 ISO / 纯数字码）")
    raw_value: str | None = Field(
        default=None,
        description="票面原文形态（如 ￥1,234.56）；推导值没有单一原文 → None",
    )
    raw_text: str = Field(description="命中的原始文本行，便于前端并排对照")
    source_line_no: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rule_id: str = ""
    rule_strength: float = 1.0
    # 置信度成分（S3-2）：锚点行 / 值行的 OCR score 与取值一致性，供 confidence.py 复算与追溯
    anchor_score: float | None = Field(default=None, ge=0.0, le=1.0)
    value_score: float | None = Field(default=None, ge=0.0, le=1.0)
    agreement: float = Field(default=1.0, ge=0.0, le=1.0)
    bbox: tuple[int, int, int, int] | None = None
    candidates: list[str] = Field(default_factory=list, description="同分候选（有冲突时不自动替换）")


class FieldError(BaseModel):
    """校验器/确认动作给字段的错误（S3-6 的 422 明细也用它）。"""

    field: str
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


class ExtractionResult(BaseModel):
    """整张票的抽取结果。"""

    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ticket_type: str = "unknown"

    def value_of(self, name: str) -> str | None:
        item = self.fields.get(name)
        return item.value if item else None