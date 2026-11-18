#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成像扰动（S1-1）。

任务卡的硬要求：合成样本必须故意含倾斜、阴影、印章遮挡，否则指标虚高、S6 的消融结论会被推翻。

两条实现纪律：
1. **几何扰动必须同步变换标注框**：旋转/透视用一个矩阵同时作用于图像与四个角点，
   再取外接矩形；否则标注会漂移，而漂移的标注比没有标注更糟。
2. **先几何、后光度、最后叠加**：印章与折痕是"盖在纸面上的东西"，不改变几何；
   放在最后画，才能真实模拟"盖住了字"。
"""
from __future__ import annotations

import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from invoice_layout import Box, Drawn
from sample_fonts import load_font

Point = tuple[float, float]


# ---------------------------------------------------------------------------
# 几何：单应矩阵
# ---------------------------------------------------------------------------
def _homography(src: list[Point], dst: list[Point]) -> np.ndarray:
    """求 3×3 单应矩阵 H，使得 dst ≈ H · src（DLT，4 组点刚好定解）。"""
    a: list[list[float]] = []
    b: list[float] = []
    for (x, y), (u, v) in zip(src, dst):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    solution, *_ = np.linalg.lstsq(np.asarray(a, dtype=float), np.asarray(b, dtype=float), rcond=None)
    return np.asarray([[solution[0], solution[1], solution[2]],
                       [solution[3], solution[4], solution[5]],
                       [solution[6], solution[7], 1.0]])


def _apply(h: np.ndarray, point: Point) -> Point:
    x, y = point
    vec = h @ np.array([x, y, 1.0])
    if abs(vec[2]) < 1e-9:
        return (float("nan"), float("nan"))
    return (vec[0] / vec[2], vec[1] / vec[2])


def _corners(box: Box) -> list[Point]:
    x1, y1, x2, y2 = box
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def _clamped_bbox(points: list[Point], width: int, height: int) -> Box:
    xs = [p[0] for p in points if not np.isnan(p[0])]
    ys = [p[1] for p in points if not np.isnan(p[1])]
    x1 = max(0, min(width - 1, int(np.floor(min(xs)))))
    y1 = max(0, min(height - 1, int(np.floor(min(ys)))))
    x2 = max(x1 + 1, min(width, int(np.ceil(max(xs)))))
    y2 = max(y1 + 1, min(height, int(np.ceil(max(ys)))))
    return (x1, y1, x2, y2)


def _warp(image: Image.Image, h_forward: np.ndarray, drawn: list[Drawn], background=(255, 255, 255)):
    """用正向矩阵变换图像（PIL 需要逆矩阵），并同步变换标注框。"""
    h_inverse = np.linalg.inv(h_forward)
    h_inverse = h_inverse / h_inverse[2, 2]
    coeffs = (
        h_inverse[0, 0], h_inverse[0, 1], h_inverse[0, 2],
        h_inverse[1, 0], h_inverse[1, 1], h_inverse[1, 2],
        h_inverse[2, 0], h_inverse[2, 1],
    )
    warped = image.transform(
        image.size, Image.Transform.PERSPECTIVE, coeffs, resample=Image.Resampling.BICUBIC,
        fillcolor=background,
    )
    moved = [
        Drawn(label=item.label, text=item.text,
              bbox=_clamped_bbox([_apply(h_forward, p) for p in _corners(item.bbox)], *image.size))
        for item in drawn
    ]
    return warped, moved


def rotate(image: Image.Image, drawn: list[Drawn], degrees: float, *, background=(255, 255, 255)):
    """绕中心旋转（不扩画布，等同"手持拍照没对准"）。"""
    if abs(degrees) < 1e-6:
        return image, drawn
    width, height = image.size
    cx, cy = width / 2, height / 2
    angle = np.radians(degrees)
    cos_a, sin_a = float(np.cos(angle)), float(np.sin(angle))
    forward = np.array([
        [cos_a, -sin_a, cx - cos_a * cx + sin_a * cy],
        [sin_a, cos_a, cy - sin_a * cx - cos_a * cy],
        [0.0, 0.0, 1.0],
    ])
    return _warp(image, forward, drawn, background)


def perspective(image: Image.Image, drawn: list[Drawn], jitter_ratio: float, rng: random.Random,
                *, background=(255, 255, 255)):
    """四角各自内缩/外扩，模拟斜拍梯形畸变。"""
    width, height = image.size
    if jitter_ratio <= 0:
        return image, drawn
    src = [(0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height))]
    dx, dy = width * jitter_ratio, height * jitter_ratio
    dst = [
        (src[0][0] + rng.uniform(0, dx), src[0][1] + rng.uniform(0, dy)),
        (src[1][0] - rng.uniform(0, dx), src[1][1] + rng.uniform(0, dy)),
        (src[2][0] - rng.uniform(0, dx), src[2][1] - rng.uniform(0, dy)),
        (src[3][0] + rng.uniform(0, dx), src[3][1] - rng.uniform(0, dy)),
    ]
    return _warp(image, _homography(src, dst), drawn, background)


# ---------------------------------------------------------------------------
# 背景合成（把票面"放到桌面上"）
# ---------------------------------------------------------------------------
_BACKGROUNDS = [
    # (名字, RGB 基色, 噪点强度, 权重) —— 红/木色刻意加权，对齐真实样本的强红背景
    ("red_desk", (146, 55, 97), 12.0, 0.34),
    ("dark_red_cloth", (90, 26, 50), 12.0, 0.16),
    ("wood_desk", (150, 110, 70), 14.0, 0.22),
    ("gray_desk", (120, 122, 126), 10.0, 0.16),
    ("blue_cloth", (70, 85, 120), 10.0, 0.06),
    ("white_table", (232, 230, 226), 6.0, 0.06),
]


def paste_on_background(image: Image.Image, drawn: list[Drawn], rng: random.Random,
                        *, pad_ratio: tuple[float, float] = (0.08, 0.22)):
    """把票面贴到带纹理的背景上，并整体平移标注框。

    这一步是"能不能做票据定位"的前提：只给纯纸面图，透视校正永远不会被真正考验。
    """
    width, height = image.size
    pad = rng.uniform(*pad_ratio)
    canvas_w = int(width * (1 + 2 * pad))
    canvas_h = int(height * (1 + 2 * pad))

    names = [item[0] for item in _BACKGROUNDS]
    weights = [item[3] for item in _BACKGROUNDS]
    name = rng.choices(names, weights=weights, k=1)[0]
    _, base, noise_sigma, _weight = next(item for item in _BACKGROUNDS if item[0] == name)
    arr = np.zeros((canvas_h, canvas_w, 3), dtype=np.float32)
    arr[:, :] = np.array(base, dtype=np.float32)
    rng_np = np.random.default_rng(rng.getrandbits(32))
    arr += rng_np.normal(0, noise_sigma, arr.shape)
    # 低通一下，模拟布料/木纹的缓变，而不是纯噪点
    bg = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")
    bg = bg.filter(ImageFilter.GaussianBlur(rng.uniform(1.0, 3.0)))
    bg = bg.filter(ImageFilter.GaussianBlur(0))  # 保持接口一致，不影响结果

    offset_x = (canvas_w - width) // 2 + rng.randint(-width // 20, width // 20)
    offset_y = (canvas_h - height) // 2 + rng.randint(-height // 20, height // 20)
    bg.paste(image, (offset_x, offset_y))

    moved = [
        Drawn(label=item.label, text=item.text,
              bbox=(item.bbox[0] + offset_x, item.bbox[1] + offset_y,
                    item.bbox[2] + offset_x, item.bbox[3] + offset_y))
        for item in drawn
    ]
    moved.append(Drawn(label="paper_zone", text=name,
                       bbox=(offset_x, offset_y, offset_x + width, offset_y + height)))
    return bg, moved


# ---------------------------------------------------------------------------
# 光度
# ---------------------------------------------------------------------------
def _to_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image, dtype=np.float32)


def _to_image(array: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), mode="RGB")


def illumination(image: Image.Image, rng: random.Random, *, min_gain: float = 0.55, max_gain: float = 1.0):
    """线性渐变 + 1–2 处椭圆阴影：模拟不均匀光照与遮挡阴影。"""
    arr = _to_array(image)
    height, width = arr.shape[:2]
    direction = rng.choice(["left", "right", "top", "bottom", "diagonal"])

    if direction in ("left", "right", "diagonal"):
        ramp = np.linspace(0, 1, width, dtype=np.float32)
        if direction == "right":
            ramp = ramp[::-1]
    else:
        ramp = np.linspace(0, 1, height, dtype=np.float32)
        if direction == "bottom":
            ramp = ramp[::-1]

    if direction == "diagonal":
        ramp2 = np.linspace(0, 1, height, dtype=np.float32)[:, None]
        ramp = np.clip((ramp[None, :] + ramp2) / 2.0, 0, 1)
    elif ramp.ndim == 1 and len(ramp) == width:
        ramp = ramp[None, :]
    else:
        ramp = ramp[:, None]

    gain = min_gain + (max_gain - min_gain) * ramp
    arr = arr * gain[..., None] if arr.ndim == 3 else arr * gain

    # 阴影：画椭圆到蒙版上再加权
    shadow = Image.new("L", (width, height), 0)
    sdraw = ImageDraw.Draw(shadow)
    for _ in range(rng.randint(1, 2)):
        cw = rng.randint(width // 4, width // 2)
        ch = rng.randint(height // 6, height // 3)
        cx = rng.randint(0, max(1, width))
        cy = rng.randint(0, max(1, height))
        sdraw.ellipse([cx - cw // 2, cy - ch // 2, cx + cw // 2, cy + ch // 2], fill=rng.randint(30, 70))
    shadow_arr = np.asarray(shadow.filter(ImageFilter.GaussianBlur(60)), dtype=np.float32) / 255.0
    arr = arr * (1.0 - 0.35 * shadow_arr[..., None])
    return _to_image(arr)


def color_cast(image: Image.Image, rng: random.Random, *, low: float = 0.85, high: float = 1.15):
    """通道增益色偏。真实样本红偏极强（红像素 33.5%），合成集必须覆盖这部分分布。"""
    arr = _to_array(image)
    gains = np.array([rng.uniform(low, high), rng.uniform(0.95, 1.05), rng.uniform(low, high)], dtype=np.float32)
    return _to_image(arr * gains[None, None, :])


def noise(image: Image.Image, sigma: float, rng: random.Random):
    if sigma <= 0:
        return image
    arr = _to_array(image)
    rng_np = np.random.default_rng(rng.getrandbits(32))
    arr = arr + rng_np.normal(0, sigma, arr.shape)
    return _to_image(arr)


def blur(image: Image.Image, radius: float):
    return image if radius <= 0 else image.filter(ImageFilter.GaussianBlur(radius))


# ---------------------------------------------------------------------------
# 叠加物（不改几何）
# ---------------------------------------------------------------------------
def overprint_seal(image: Image.Image, rng: random.Random, *, text_area: int,
                   cover_ratio: tuple[float, float] = (0.05, 0.15)) -> tuple[Image.Image, Drawn]:
    """盖一个半透明红章，覆盖文本区域的 5%–15%。"""
    width, height = image.size
    target_area = text_area * rng.uniform(*cover_ratio)
    radius = int(np.sqrt(target_area / np.pi))
    radius = max(28, min(radius, min(width, height) // 4))
    cx = rng.randint(radius + 10, max(radius + 11, width - radius - 10))
    cy = rng.randint(radius + 10, max(radius + 11, height - radius - 10))

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                  outline=(200, 30, 40, 175), width=max(3, radius // 12))
    odraw.ellipse([cx - int(radius * 0.78), cy - int(radius * 0.78),
                   cx + int(radius * 0.78), cy + int(radius * 0.78)],
                  outline=(200, 30, 40, 110), width=max(1, radius // 40))
    font = load_font(max(10, radius // 5))
    odraw.text((cx, cy), "发票专用章", font=font, fill=(200, 30, 40, 190), anchor="mm")

    merged = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    return merged, Drawn(label="seal_zone", text="发票专用章", bbox=box)


def crease(image: Image.Image, rng: random.Random) -> tuple[Image.Image, Drawn]:
    """折痕：一条浅色折线 + 旁边略深的阴影线。"""
    width, height = image.size
    y = rng.randint(height // 5, height * 4 // 5)
    skew = rng.randint(-height // 12, height // 12)
    draw = ImageDraw.Draw(image)
    draw.line([(0, y), (width, y + skew)], fill=(235, 235, 235), width=3)
    draw.line([(0, y + 3), (width, y + skew + 3)], fill=(150, 150, 150), width=1)
    box = (0, max(0, min(y, y + skew)), width, min(height, max(y, y + skew) + 4))
    return image, Drawn(label="crease", text="", bbox=box)