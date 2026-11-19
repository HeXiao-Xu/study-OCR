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