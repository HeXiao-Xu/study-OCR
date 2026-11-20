"""S3-3 裁剪原语：外扩、clamp、缩放、坐标映射。

这些函数一旦错，表现是"重识别拿到的图不是那一行"——很难从结果反推，所以用纯 numpy 用例钉死。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.core.exceptions import ValidationFailedError
from app.ocr import refine


def _image(width: int = 200, height: int = 100) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_points_to_bbox_takes_outer_rectangle() -> None:
    points = [[10, 20], [90, 18], [92, 44], [8, 46]]
    assert refine.points_to_bbox(points) == (8, 18, 92, 46)


def test_expand_bbox_uses_max_of_pixels_and_ratio() -> None:
    # 行高 26 → 10% = 2.6 < 8 → 用 8px
    assert refine.expand_bbox((100, 100, 300, 126), width=800, height=600) == (92, 92, 308, 134)
    # 行高 100 → 10% = 10 > 8 → 用 10px
    assert refine.expand_bbox((100, 100, 300, 200), width=800, height=600) == (90, 90, 310, 210)


def test_expand_bbox_clamps_to_image_edges() -> None:
    assert refine.expand_bbox((2, 2, 30, 20), width=200, height=100) == (0, 0, 38, 28)


def test_expand_bbox_rejects_degenerate_bbox() -> None:
    with pytest.raises(ValidationFailedError):
        refine.expand_bbox((30, 10, 30, 40), width=200, height=100)


def test_crop_region_returns_crop_and_actual_bbox() -> None:
    image = _image()

    crop, bbox = refine.crop_region(image, (50, 40, 150, 70))

    assert bbox == (42, 32, 158, 78)
    assert crop.shape[:2] == (bbox[3] - bbox[1], bbox[2] - bbox[0])


def test_crop_region_outside_image_raises_422() -> None:
    with pytest.raises(ValidationFailedError):
        refine.crop_region(_image(), (500, 500, 600, 560))


def test_crop_region_negative_outside_raises() -> None:
    with pytest.raises(ValidationFailedError):
        refine.crop_region(_image(), (-50, -50, -20, -20))   # 与图无交集


def test_upscale_doubles_and_caps_long_side() -> None:
    crop = np.zeros((10, 20, 3), dtype=np.uint8)
    assert refine.upscale(crop, factor=2.0).shape[:2] == (20, 40)

    wide = np.zeros((10, 800, 3), dtype=np.uint8)
    enlarged = refine.upscale(wide, factor=2.0, max_side=1024)
    assert enlarged.shape[:2] == (13, 1024)      # 10×1.28=12.8 → 13


def test_reread_box_uses_recognizer(monkeypatch) -> None:
    calls: list[np.ndarray] = []

    def fake_recognize(crop, engine=None):
        calls.append(crop)
        return "¥1,234.56", 0.97

    monkeypatch.setattr(refine, "recognize_line", fake_recognize)

    candidate = refine.reread_box(_image(), [[50, 40], [150, 40], [150, 70], [50, 70]])

    assert candidate.text == "¥1,234.56"
    assert candidate.score == 0.97
    assert candidate.bbox == (42, 32, 158, 78)
    assert calls and calls[0].shape[0] == (78 - 32) * 2      # 已放大


def test_bbox_mapping_identity_when_no_matrix() -> None:
    assert refine.bbox_to_original((10, 20, 30, 40), None) == (10, 20, 30, 40)
    assert refine.bbox_from_original((10, 20, 30, 40), None) == (10, 20, 30, 40)


def test_bbox_mapping_scales_and_round_trips() -> None:
    matrix = np.array([[2.0, 0.0, 5.0], [0.0, 2.0, -3.0], [0.0, 0.0, 1.0]])

    assert refine.bbox_to_original((10, 20, 30, 40), matrix) == (25, 37, 65, 77)
    assert refine.bbox_from_original((25, 37, 65, 77), matrix) == (10, 20, 30, 40)