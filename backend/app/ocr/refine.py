"""局部重识别原语（S3-3）：按 OCR 行框裁剪、放大、单行识别。

为什么单独一层：裁剪边界（越界/退化）是 500 与 422 的分水岭，必须可单测；
坐标映射（校正图 ↔ 原图）是 S3-5 画框与本卡共用的唯一实现。

坐标口径（D-060）：
- `ocr_lines.box_json` = **校正图**四点（识别发生在校正图上）；
- `invoice_fields.bbox_json` = **原图**外接框（前端画框只认它）；
- 字段级重识别直接吃 `ocr_lines` 的四点，不做逆映射。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from app.core.exceptions import ValidationFailedError
from app.ocr.recognizer import recognize_line

__all__ = [
    "RereadCandidate",
    "bbox_from_original",
    "bbox_to_original",
    "crop_region",
    "expand_bbox",
    "points_to_bbox",
    "reread_box",
    "upscale",
]

# 以下常量只是函数默认值；运行时由 Settings（REREAD_*）传入，便于调参与消融
PAD_MIN_PX = 8
PAD_RATIO = 0.10
SCALE = 2.0
MAX_SIDE = 1024
MIN_CROP_PX = 8


@dataclass(frozen=True)
class RereadCandidate:
    text: str
    score: float
    bbox: tuple[int, int, int, int]
    scale: float


def points_to_bbox(points: Sequence[Sequence[float]]) -> tuple[int, int, int, int]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return int(round(min(xs))), int(round(min(ys))), int(round(max(xs))), int(round(max(ys)))


def expand_bbox(
    bbox: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
    pad_min_px: int = PAD_MIN_PX,
    pad_ratio: float = PAD_RATIO,
) -> tuple[int, int, int, int]:
    """四周外扩后 clamp 到图内。

    以**行高**为基准算 10%：票据行很宽（可达 700px），按宽度 10% 扩会把相邻行也框进来。
    """
    x1, y1, x2, y2 = (int(value) for value in bbox)
    if x2 <= x1 or y2 <= y1:
        raise ValidationFailedError("bbox 非法：宽高必须为正")
    pad = max(int(pad_min_px), int(round((y2 - y1) * pad_ratio)))
    return (
        max(0, x1 - pad),
        max(0, y1 - pad),
        min(int(width), x2 + pad),
        min(int(height), y2 + pad),
    )


def crop_region(
    image: np.ndarray,
    bbox: tuple[int, int, int, int],
    *,
    pad_min_px: int = PAD_MIN_PX,
    pad_ratio: float = PAD_RATIO,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """裁剪并返回 (crop, 实际框)。越界/退化一律 422 语义（`ValidationFailedError`）。"""
    if image is None or getattr(image, "ndim", 0) < 2:
        raise ValidationFailedError("影像不可用，无法裁剪")
    height, width = image.shape[:2]
    actual = expand_bbox(
        bbox, width=width, height=height, pad_min_px=pad_min_px, pad_ratio=pad_ratio
    )
    x1, y1, x2, y2 = actual
    if x2 - x1 < MIN_CROP_PX or y2 - y1 < MIN_CROP_PX:
        raise ValidationFailedError("裁剪区域为空或过小（bbox 越界？）")
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValidationFailedError("裁剪区域为空（bbox 越界？）")
    return crop, actual


def upscale(crop: np.ndarray, *, factor: float = SCALE, max_side: int = MAX_SIDE) -> np.ndarray:
    height, width = crop.shape[:2]
    longest = max(height, width)
    if longest == 0:
        raise ValidationFailedError("裁剪图为空")
    scale = min(float(factor), max_side / longest)
    if scale <= 1.0:
        return crop
    return cv2.resize(
        crop,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_CUBIC,
    )


def reread_box(
    image: np.ndarray,
    box: Sequence[Sequence[float]] | tuple[int, int, int, int],
    *,
    engine=None,
    factor: float = SCALE,
    pad_min_px: int = PAD_MIN_PX,
    pad_ratio: float = PAD_RATIO,
) -> RereadCandidate:
    """把一个 OCR 行框裁剪放大后重识别。`box` 可以是四点（`ocr_lines.box_json`）或外接框。"""
    if len(box) == 4 and isinstance(box[0], (list, tuple)):
        bbox = points_to_bbox(box)          # type: ignore[arg-type]
    else:
        bbox = tuple(int(value) for value in box)   # type: ignore[arg-type]
    crop, actual = crop_region(image, bbox, pad_min_px=pad_min_px, pad_ratio=pad_ratio)
    enlarged = upscale(crop, factor=factor)
    text, score = recognize_line(enlarged, engine)
    return RereadCandidate(text=text, score=float(score), bbox=actual, scale=factor)


def _apply(bbox: tuple[int, int, int, int], matrix: np.ndarray) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    corners = np.array([[x1, y1, 1.0], [x2, y1, 1.0], [x2, y2, 1.0], [x1, y2, 1.0]])
    mapped = corners @ matrix.T
    xs = mapped[:, 0] / mapped[:, 2]
    ys = mapped[:, 1] / mapped[:, 2]
    return int(round(xs.min())), int(round(ys.min())), int(round(xs.max())), int(round(ys.max()))


def bbox_to_original(
    bbox: tuple[int, int, int, int], to_original: np.ndarray | None
) -> tuple[int, int, int, int]:
    """校正图 → 原图。PDF 没有预处理，矩阵为 None → 恒等。"""
    if to_original is None:
        return bbox
    return _apply(bbox, np.asarray(to_original, dtype=float))


def bbox_from_original(
    bbox: tuple[int, int, int, int], to_original: np.ndarray | None
) -> tuple[int, int, int, int]:
    """原图 → 校正图（区域裁剪/画框换算要用）。"""
    if to_original is None:
        return bbox
    return _apply(bbox, np.linalg.inv(np.asarray(to_original, dtype=float)))