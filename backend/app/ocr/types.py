"""OCR 输出契约（S1-3）。

这一层是后续所有模块的**输入契约**：S1-4 版面分块、S1-5 字段抽取、S3 核对页都吃这个结构。
所以用 Pydantic 定义、并配契约测试 —— 库升级把返回结构改掉时，测试先炸，而不是让上层悄悄跑错。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


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
        """四点 → 外接矩形 [x1,y1,x2,y2]（AGENTS §5 的字段层格式）。"""
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
    det_params: dict = Field(default_factory=dict, description="实际使用的检测参数，写进 ocr_jobs.params_json")

    @property
    def is_empty(self) -> bool:
        return not self.lines

    @property
    def mean_score(self) -> float:
        return sum(line.score for line in self.lines) / len(self.lines) if self.lines else 0.0

    def find_by_keyword(self, keyword: str) -> list[OcrLine]:
        """按关键字筛行——S1-5 的"关键字锚定"会用同一套筛选逻辑。"""
        return [line for line in self.lines if keyword in line.text]

class ExtractedField(BaseModel):
    """一个抽取出来的字段（S1-5）。**保留来源行号与规则 id，便于追溯**。"""

    name: str
    value: str = Field(description="归一化后的值（金额为 Decimal 的字符串、日期为 ISO）")
    raw_text: str = Field(description="票面上的原始文本，便于前端并排对照")
    source_line_no: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rule_id: str = ""
    rule_strength: float = 1.0
    bbox: tuple[int, int, int, int] | None = None
    candidates: list[str] = Field(default_factory=list, description="同分候选（有冲突时不自动替换）")


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
