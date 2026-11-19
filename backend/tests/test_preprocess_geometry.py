"""S1-2 预处理：几何（定位 / 透视 / 降级链 / 坐标回算）。

核心断言：**标注与坐标不能漂**。预处理在"校正图"上工作，但下游要拿"原图"显示框，
所以 `to_original` 必须能把校正图的角点准确映回检测到的四角点。
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.ocr.preprocess import PreprocessOptions, preprocess


def _paper_on_desk(quad: np.ndarray, size: tuple[int, int] = (900, 700),
                   desk: int = 35, paper: int = 235) -> np.ndarray:
    """深色桌面上放一张**斜置**的白纸（已知四角），用于验证定位与透视。"""
    width, height = size
    image = np.full((height, width, 3), desk, dtype=np.uint8)
    cv2.fillPoly(image, [quad.astype(np.int32)], (paper, paper, paper))
    # 纸上画几条"文字"横线，保证 Otsu 能分出纸面
    for offset in (0.25, 0.45, 0.65):
        y = int(quad[:, 1].min() + (quad[:, 1].max() - quad[:, 1].min()) * offset)
        cv2.line(image, (int(quad[:, 0].min() + 40), y), (int(quad[:, 0].max() - 40), y), (20, 20, 20), 3)
    return image


_QUAD = np.array([[130, 90], [770, 150], [800, 620], [95, 560]], dtype=np.float32)


def _corner_error(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)).max())


def test_quad_detected_within_tolerance() -> None:
    image = _paper_on_desk(_QUAD)
    result = preprocess(image, PreprocessOptions(illum=False, detect_seal=False, denoise=False))

    assert result.steps["rectify"] == "quad_perspective"
    assert result.quad is not None
    diagonal = float(np.hypot(*image.shape[:2]))
    error = _corner_error(np.array(result.quad), _QUAD)
    assert error <= 0.02 * diagonal, f"四角点误差 {error:.1f}px 超过 2% 对角线"


def test_to_original_maps_corrected_corners_back() -> None:
    image = _paper_on_desk(_QUAD)
    result = preprocess(image, PreprocessOptions(illum=False, detect_seal=False, denoise=False))

    height, width = result.image.shape[:2]
    corrected_corners = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                                 dtype=np.float64).reshape(-1, 1, 2)
    mapped = cv2.perspectiveTransform(corrected_corners, result.to_original).reshape(-1, 2)

    diagonal = float(np.hypot(*image.shape[:2]))
    error = _corner_error(mapped, np.array(result.quad))
    assert error <= 0.02 * diagonal, f"to_original 回算误差 {error:.1f}px"


def test_corrected_image_is_filled_by_paper() -> None:
    """透视校正后，纸面应基本铺满输出图（说明确实按纸的边界裁了）。"""
    image = _paper_on_desk(_QUAD)
    result = preprocess(image, PreprocessOptions(illum=False, detect_seal=False, denoise=False))

    gray = cv2.cvtColor(result.image, cv2.COLOR_BGR2GRAY)
    bright_ratio = float((gray > 180).mean())
    assert bright_ratio > 0.75, f"校正后纸面只占 {bright_ratio:.0%}，说明透视没对准"


def test_rectify_disabled_keeps_original_geometry() -> None:
    image = _paper_on_desk(_QUAD)
    result = preprocess(image, PreprocessOptions(rectify=False, illum=False, detect_seal=False, denoise=False))

    assert result.steps["rectify"] == "disabled"
    assert result.image.shape[:2] == image.shape[:2]
    np.testing.assert_allclose(result.to_original, np.eye(3), atol=1e-9)


def test_no_quad_falls_back_to_min_area_rect() -> None:
    """一个只有旋转、没有明显四角的场景：应落到 minAreaRect 而不是直接放弃。"""
    image = np.full((600, 800, 3), 30, dtype=np.uint8)
    rotated = cv2.getRotationMatrix2D((400, 300), 8.0, 1.0)
    paper = np.zeros((600, 800, 3), dtype=np.uint8)
    cv2.rectangle(paper, (120, 90), (700, 520), (240, 240, 240), -1)
    image = cv2.warpAffine(paper, rotated, (800, 600), borderValue=(30, 30, 30))

    result = preprocess(image, PreprocessOptions(illum=False, detect_seal=False, denoise=False))
    assert result.steps["rectify"] in {"quad_perspective", "min_area_rect"}
    # 无论走哪条路径，都必须产出可用图像且坐标矩阵有效
    assert result.image.size > 0
    assert result.to_original.shape == (3, 3)
    assert abs(np.linalg.det(result.to_original)) > 1e-9


@pytest.mark.parametrize("options", [
    PreprocessOptions(rectify=False),
    PreprocessOptions(illum=False, detect_seal=False),
    PreprocessOptions(rotate=False, denoise=False),
    PreprocessOptions(binarize=True),
])
def test_switch_combinations_never_raise(options: PreprocessOptions) -> None:
    """S6 会逐个关掉开关做消融，任何组合都不能抛异常。"""
    image = _paper_on_desk(_QUAD)
    result = preprocess(image, options)
    assert result.image.ndim == 3
    assert result.quality_flag in {"ok", "low"}