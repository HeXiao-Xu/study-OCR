"""S1-1 几何扰动的单元测试。

最关键的一条：**旋转后标注框必须跟着字走**。
如果框没跟着变换，生成出来的数据集就是"图是歪的、框是正的"，
S1 的所有指标都会莫名其妙地低——这类 bug 从数字上看不出来，只能靠单测钉死。
"""
from __future__ import annotations

import random

import numpy as np
from PIL import Image, ImageDraw

from invoice_layout import Canvas, Drawn
from perturb import _apply, _corners, _homography


def _black_square(size: int = 200, box: tuple[int, int, int, int] = (10, 10, 50, 50)) -> tuple[Image.Image, Drawn]:
    image = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle(list(box), fill=(0, 0, 0))
    return image, Drawn(label="square", text="", bbox=box)


def test_homography_identity() -> None:
    points = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
    matrix = _homography(points, points)
    for x, y in points:
        mapped = _apply(matrix, (x, y))
        assert abs(mapped[0] - x) < 1e-6
        assert abs(mapped[1] - y) < 1e-6


def test_rotate_moves_pixels_and_bbox_together() -> None:
    """90° 旋转：左上角的黑块应转到右上角，且框同步落到新位置。"""
    image, square = _black_square()
    from perturb import rotate

    rotated, moved = rotate(image, [square], 90.0)
    assert len(moved) == 1
    new_bbox = moved[0].bbox

    # 黑块中心的新位置：按旋转矩阵算，应落在右上象限
    arr = np.asarray(rotated.convert("L"))
    ys, xs = np.where(arr < 128)
    assert len(xs) > 0, "旋转后黑块消失了"
    cx, cy = float(xs.mean()), float(ys.mean())
    assert cx > rotated.width / 2, "90° 旋转后应位于右半侧"
    assert cy < rotated.height / 2, "90° 旋转后应位于上半侧"

    # bbox 必须罩住实际墨迹（允许 2px 误差）
    x1, y1, x2, y2 = new_bbox
    assert x1 - 2 <= xs.min() and xs.max() <= x2 + 2
    assert y1 - 2 <= ys.min() and ys.max() <= y2 + 2


def test_rotate_zero_returns_same_object() -> None:
    image, square = _black_square()
    from perturb import rotate

    same_image, same_drawn = rotate(image, [square], 0.0)
    assert same_image is image
    assert same_drawn[0] is square


def test_perspective_keeps_bbox_on_ink() -> None:
    image, square = _black_square()
    from perturb import perspective

    warped, moved = perspective(image, [square], 0.05, random.Random(7))
    arr = np.asarray(warped.convert("L"))
    ys, xs = np.where(arr < 128)
    x1, y1, x2, y2 = moved[0].bbox
    # 外接矩形必然略微大于墨迹，但不该大出太多（>15px 说明变换矩阵用错了）
    assert x1 - 1 <= xs.min() and xs.max() <= x2 + 1
    assert y1 - 1 <= ys.min() and ys.max() <= y2 + 1
    assert (x2 - x1) <= (xs.max() - xs.min()) + 15
    assert (y2 - y1) <= (ys.max() - ys.min()) + 15


def test_background_paste_shifts_bboxes() -> None:
    image, square = _black_square()
    from perturb import paste_on_background

    composed, moved = paste_on_background(image, [square], random.Random(11), pad_ratio=(0.2, 0.2))
    assert composed.width > image.width
    paper = next(item for item in moved if item.label == "paper_zone")
    x1, y1, x2, y2 = paper.bbox
    assert (x2 - x1, y2 - y1) == image.size
    # 票面区域的偏移量应等于黑块框的偏移量
    square_moved = next(item for item in moved if item.label == "square")
    assert square_moved.bbox[0] - square.bbox[0] == x1
    assert square_moved.bbox[1] - square.bbox[1] == y1


def test_crease_returns_bbox_within_image() -> None:
    image, _ = _black_square()
    from perturb import crease

    out, drawn = crease(image, random.Random(3))
    assert out.size == image.size
    x1, y1, x2, y2 = drawn.bbox
    assert 0 <= x1 < x2 <= out.width
    assert 0 <= y1 < y2 <= out.height


def test_corners_helper_matches_bbox() -> None:
    assert _corners((1, 2, 3, 4)) == [(1, 2), (3, 2), (3, 4), (1, 4)]