"""票据图像预处理（S1-2）。

把任意来源的票据图（横拍/竖拍/斜拍/带桌面背景/偏暗/有印章）整成一个**统一、可复现**的形态，
供 S1-3 的检测识别使用。

三条不可妥协的约束（AGENTS.md §5）：
1. **EXIF 方向修正必须最先做**，否则整条链路全错；
2. **失败必须降级并打标，绝不抛异常**——真实场景里"定位失败"是常态，不是异常；
3. **不做无收益的强处理**：二值化默认关（DBNet 用自然彩色图训练），印章只标记不 inpaint。

另外返回 `to_original`（3×3 单应矩阵，校正图 → 原图）：S1-3 在校正图上检测，
但前端必须拿**原图**显示高亮框，没有它坐标就对不上（S3-5 核对页的前提）。
"""
from __future__ import annotations

import io as _io
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

__all__ = ["PreprocessOptions", "QualityMetrics", "PreprocessResult", "preprocess"]


# ---------------------------------------------------------------------------
# 配置与结果
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PreprocessOptions:
    """每一步都能单独关掉——S6 的消融实验就靠这些开关。"""

    exif: bool = True
    resize: bool = True
    denoise: bool = True
    rectify: bool = True          # 四角点 + 透视
    rotate: bool = True           # 残差小角度
    illum: bool = True
    detect_seal: bool = True
    binarize: bool = False        # 默认关：强二值化是 DBNet 的域外输入
    max_side: int = 1600          # **检测**前的缩放上限（越小的图检测越快，但识别不受影响）
    warp_max_side: int = 2400     # 校正后的长边上限：比检测上限更宽松，避免把字缩小
    min_side: int = 0             # 校正后短边下限（0=不放大）。小票放大不增加信息，默认关

    # 定位与几何阈值（不散落在代码里）
    min_area_ratio: float = 0.18      # 纸面占画面 ≥18%：真实手机拍照常见 20–35%
    min_interior_angle: float = 50.0
    max_interior_angle: float = 130.0
    deskew_min_deg: float = 1.5
    min_area_rect_ratio: float = 0.15

    # 印章与质量
    seal_min_area_ratio: float = 0.003
    low_brightness: float = 60.0
    overexposed_ratio: float = 0.95     # 几乎全白（空白/严重过曝）才算；白底文件的正常占比就有 40–60%
    low_contrast: float = 25.0
    low_sharpness: float = 80.0


@dataclass
class QualityMetrics:
    brightness: float
    contrast: float
    sharpness: float
    seal_ratio: float
    saturated_ratio: float = 0.0
    reasons: list[str] = field(default_factory=list)   # 判为 low 的具体原因

    @property
    def is_low(self) -> bool:
        return bool(self.reasons)


@dataclass
class PreprocessResult:
    image: np.ndarray                                  # BGR，已校正
    original_size: tuple[int, int]                     # (宽, 高)
    quality_flag: str                                  # "ok" / "low"
    quality: QualityMetrics
    quad: list[list[float]] | None                     # 原图坐标四角点（顺时针）
    rotate_deg: float
    seal_boxes: list[tuple[int, int, int, int]]        # 原图坐标
    warnings: list[str]
    steps: dict[str, bool]
    timings_ms: dict[str, float]
    to_original: np.ndarray                            # 3×3：校正图 → 原图
    scale: float


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------
def _to_pil(source: str | Path | bytes | np.ndarray) -> tuple[Image.Image, bool]:
    """统一成 PIL RGB。返回 (图像, 是否来自带 EXIF 的文件)。"""
    if isinstance(source, np.ndarray):
        array = source
        if array.ndim == 2:
            array = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
        return Image.fromarray(cv2.cvtColor(array, cv2.COLOR_BGR2RGB)), False
    if isinstance(source, (bytes, bytearray)):
        return Image.open(_io.BytesIO(source)).convert("RGB"), True
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"图像不存在：{path}")
    return Image.open(path).convert("RGB"), True


def _load(source: str | Path | bytes | np.ndarray, options: PreprocessOptions) -> tuple[np.ndarray, list[str]]:
    warnings: list[str] = []
    pil, has_file = _to_pil(source)
    if options.exif and has_file:
        # 手机拍摄的图常带 Orientation 标记；不解 EXIF 整张会躺倒，检测全废
        before = pil.size
        pil = ImageOps.exif_transpose(pil) or pil
        if pil.size != before:
            warnings.append("exif_rotated")
    rgb = np.asarray(pil.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), warnings


def _resize_max_side(image: np.ndarray, max_side: int) -> tuple[np.ndarray, float, bool]:
    """只缩不放，返回 (图, 缩放比, 是否缩放)。"""
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image, 1.0, False
    scale = max_side / float(longest)
    resized = cv2.resize(image, (int(round(width * scale)), int(round(height * scale))),
                         interpolation=cv2.INTER_AREA)
    return resized, scale, True


# ---------------------------------------------------------------------------
# 票据定位
# ---------------------------------------------------------------------------
def _gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def _order_quad(points: np.ndarray) -> np.ndarray:
    """顺时针排序：左上 → 右上 → 右下 → 左下。"""
    pts = points.reshape(4, 2).astype(np.float32)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape(-1)
    ordered[0] = pts[np.argmin(sums)]      # 左上
    ordered[2] = pts[np.argmax(sums)]      # 右下
    ordered[1] = pts[np.argmin(diffs)]     # 右上（y-x 最小）
    ordered[3] = pts[np.argmax(diffs)]     # 左下
    return ordered


def _interior_angles(quad: np.ndarray) -> list[float]:
    angles: list[float] = []
    for i in range(4):
        prev_pt = quad[(i - 1) % 4]
        cur = quad[i]
        next_pt = quad[(i + 1) % 4]
        v1 = prev_pt - cur
        v2 = next_pt - cur
        denominator = (np.linalg.norm(v1) * np.linalg.norm(v2)) or 1e-6
        cosine = float(np.clip(np.dot(v1, v2) / denominator, -1.0, 1.0))
        angles.append(float(np.degrees(np.arccos(cosine))))
    return angles


def _is_convex(quad: np.ndarray) -> bool:
    return bool(cv2.isContourConvex(quad.reshape(-1, 1, 2).astype(np.int32)))


def _is_simple_convex(quad: np.ndarray) -> bool:
    """简单凸四边形：四个连续叉积同号（排除自交的"蝴蝶结"）。

    为什么必须单独判：`cv2.isContourConvex` 对**已排序**的点不保证排除自交；
    实测一张行程单被排成 [[0,497],[1199,0],[1199,699],[0,498]]——自交、面积恰好半幅，
    所有面积/角度阈值都能过，warp 出来的图 OCR 一行都读不出（20 → 0）。
    """
    signs = []
    points = quad.reshape(4, 2).astype(np.float64)
    for index in range(4):
        a = points[index]
        b = points[(index + 1) % 4]
        c = points[(index + 2) % 4]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) < 1e-9:
            return False
        signs.append(np.sign(cross))
    return len(set(signs)) == 1


def _four_extreme_points(contour: np.ndarray) -> np.ndarray:
    """从任意轮廓取"最像四角"的四个点：x+y 最小/最大、x−y 最大/最小。

    透视或旋转后的票据轮廓，`approxPolyDP` 常给出 6–8 个拐点（边缘毛刺、阴影、印章粘连），
    死等"正好 4 点"会把整张票判为失败。取四个极值点是经典兜底，对凸四边形是精确的。
    """
    points = contour.reshape(-1, 2).astype(np.float32)
    sums = points.sum(axis=1)
    diffs = points[:, 0] - points[:, 1]
    return np.array([points[sums.argmin()], points[diffs.argmax()],
                     points[sums.argmax()], points[diffs.argmin()]], dtype=np.float32)


def _interior_is_paper(quad: np.ndarray, gray: np.ndarray) -> tuple[bool, str]:
    """四边形内部是否比四周明显更亮（票据通常比桌面亮）。

    比"Otsu 亮区覆盖率"稳：带光照渐变的纸面会被 Otsu 从中间劈开，覆盖率只有 0.5，
    但它依然是纸；而"整帧噪声"内外亮度几乎相同，用亮度差判就非常干脆。
    """
    height, width = gray.shape[:2]
    inside_mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(inside_mask, [quad.reshape(-1, 1, 2).astype(np.int32)], 255)
    grown = cv2.dilate(inside_mask, np.ones((15, 15), np.uint8))
    ring = (grown > 0) & (inside_mask == 0)

    inside = gray[inside_mask > 0]
    outside = gray[ring]
    if inside.size < 100:
        return False, "quad_too_tiny"
    if outside.size < 0.02 * height * width:
        # 四边形几乎覆盖整幅：要么是"整幅就是纸"的截图形态，要么是"整帧噪声"。
        # 用 Otsu 亮区占比区分：真纸面应大面积为亮，噪声各半。
        threshold, _ = cv2.threshold(cv2.GaussianBlur(gray, (5, 5), 0), 0, 255,
                                     cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        bright_ratio = float((inside >= threshold).mean())
        # 真纸面不仅"大面积为亮"，而且**绝对亮度够高**；噪声图的 Otsu 阈值会掉到低端，
        # 单看 bright_ratio 也能过（实测 0.9），必须再加一道绝对亮度门槛。
        if bright_ratio < 0.55 or float(inside.mean()) < 150.0:
            return False, f"frame_not_paper:{bright_ratio:.2f}/{inside.mean():.0f}"
        return True, "quad_covers_frame"
    delta = float(inside.mean()) - float(outside.mean())
    if delta < 12.0:
        return False, f"not_brighter:{delta:.0f}"
    return True, f"brighter:{delta:.0f}"


def _accept_quad(quad: np.ndarray, contour_area: float, options: PreprocessOptions,
                 total_area: float, gray: np.ndarray | None = None) -> tuple[np.ndarray | None, str]:
    """四项几何校验 + 「四边形不能比轮廓小太多」+ 「里面得是纸面」。"""
    if not _is_simple_convex(quad):
        return None, "self_intersecting"
    # 退化检测：四个角点必须真的"散开"。实测一张行程单被检成 (0,497)(1199,0)(1199,699)(0,498)——
    # 左下两点只差 1px，形状其实是三角形，却因为"面积 50%、角度正常"通过了所有阈值，
    # warp 后 OCR 一行都读不出（20 → 0）。
    points = quad.reshape(4, 2).astype(np.float64)
    edges = [float(np.linalg.norm(points[(i + 1) % 4] - points[i])) for i in range(4)]
    longest = max(edges)
    if longest < 8 or min(edges) < max(12.0, 0.12 * longest):
        return None, f"degenerate_quad:{min(edges):.0f}/{longest:.0f}"
    # 对边长度不能差太多（透视允许一定差异，但不会一条边是另一条的 2.5 倍）
    if abs(edges[0] - edges[2]) > 0.6 * max(edges[0], edges[2]) or \
            abs(edges[1] - edges[3]) > 0.6 * max(edges[1], edges[3]):
        return None, "unbalanced_edges"
    angles = _interior_angles(quad)
    if min(angles) < options.min_interior_angle or max(angles) > options.max_interior_angle:
        return None, "bad_angle"
    polygon_area = float(cv2.contourArea(quad.reshape(-1, 1, 2).astype(np.float32)))
    ratio = polygon_area / total_area
    if ratio < options.min_area_ratio:
        return None, f"quad_small:{ratio:.2f}"
    if contour_area > 0 and polygon_area / contour_area < 0.75:
        return None, "quad_much_smaller_than_contour"
    if gray is not None:
        is_paper, why = _interior_is_paper(quad, gray)
        if not is_paper:
            return None, why
    return quad, f"ok:{ratio:.2f}"


def _quad_from_contour(contour: np.ndarray, options: PreprocessOptions, total_area: float,
                       gray: np.ndarray | None = None) -> tuple[np.ndarray | None, str]:
    """把一个轮廓试成四角点：面积比 → 四边形 → 凸 → 内角。"""
    area_ratio = cv2.contourArea(contour) / total_area
    if area_ratio < options.min_area_ratio:
        return None, f"area_small:{area_ratio:.2f}"
    perimeter = cv2.arcLength(contour, True)
    if perimeter <= 0:
        return None, "degenerate"
    contour_area = float(cv2.contourArea(contour))

    for eps_ratio in (0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(contour, eps_ratio * perimeter, True)
        if len(approx) != 4:
            continue
        quad, why = _accept_quad(_order_quad(approx), contour_area, options, total_area, gray)
        if quad is not None:
            return quad, why

    # 兜底一：先取凸包再近似（去掉毛刺后更容易得到 4 点）
    hull = cv2.convexHull(contour)
    hull_perimeter = cv2.arcLength(hull, True) or 1
    for eps_ratio in (0.02, 0.04):
        approx = cv2.approxPolyDP(hull, eps_ratio * hull_perimeter, True)
        if len(approx) != 4:
            continue
        quad, why = _accept_quad(_order_quad(approx), contour_area, options, total_area, gray)
        if quad is not None:
            return quad, why

    # 兜底二：四点极值
    quad, why = _accept_quad(_order_quad(_four_extreme_points(hull)), contour_area, options, total_area, gray)
    if quad is not None:
        return quad, why
    return None, "not_quad"


def _candidate_masks(image: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """两路候选：① 纸面亮区（Otsu）；② 边缘（Canny + 膨胀）。

    单用 Canny 的问题：票据边框是一条**细线**，`contourArea` 算出来是线本身的面积
    （实测最大候选只有 5.6%），于是"面积占比 ≥35%"永远不满足 —— 首版就是这样把 23/60 张判成失败的。
    Otsu 的亮区掩膜给的是**实心纸面**，面积才是真正想要的。
    """
    gray = _gray(image)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    masks: list[tuple[str, np.ndarray]] = []

    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append(("otsu", otsu))

    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    masks.append(("edge", edges))
    return masks


def _find_quad(image: np.ndarray, options: PreprocessOptions) -> tuple[np.ndarray | None, list[str]]:
    """找票据四角。返回 (四角点, 诊断信息)；找不到返回 (None, 原因)。"""
    total_area = float(image.shape[0] * image.shape[1])
    notes: list[str] = []
    candidates: list[tuple[float, np.ndarray, str]] = []
    masks = dict(_candidate_masks(image))
    gray = _gray(image)

    for mask_name, mask in masks.items():
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=2)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            quad, why = _quad_from_contour(contour, options, total_area, gray)
            if quad is None:
                notes.append(f"{mask_name}:{why}")
                continue
            area_ratio = cv2.contourArea(quad.reshape(-1, 1, 2).astype(np.float32)) / total_area
            candidates.append((area_ratio, quad, f"{mask_name}:{why}"))

    if not candidates:
        return None, notes[:4] or ["no_candidate"]
    # 面积最大者胜出：纸面一定比版面里的表格/二维码大
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_ratio, best_quad, best_why = candidates[0]
    notes.append(f"best:{best_why}")
    return best_quad, notes


# ---------------------------------------------------------------------------
# 几何变换（全部返回 forward 矩阵 current → next，便于合成 to_original）
# ---------------------------------------------------------------------------
def _quad_is_frame(quad: np.ndarray, image: np.ndarray, tolerance: float = 0.005) -> bool:
    """四角点是否几乎就是图像四角（±0.5%）。

    这种情况再走一遍透视变换只是白白重采样一次：实测 600×1000 的票面会被插值糊掉一点点，
    正好把加粗的"票价"数字抹花（OCR 从 3/3 掉到 2/3）。
    """
    height, width = image.shape[:2]
    frame = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    deviation = np.abs(quad - frame).max()
    return deviation <= tolerance * max(width, height)


def _warp_perspective(image: np.ndarray, quad: np.ndarray, max_side: int) -> tuple[np.ndarray, np.ndarray]:
    top_left, top_right, bottom_right, bottom_left = quad
    width = max(np.linalg.norm(top_right - top_left), cv2.norm(bottom_right, bottom_left))
    height = max(np.linalg.norm(bottom_left - top_left), cv2.norm(bottom_right, top_right))
    width, height = max(8, int(round(width))), max(8, int(round(height)))
    # 输出不得超过源图尺寸：超过只会把空白/边缘复制进来并拉伸内容
    source_height, source_width = image.shape[:2]
    width, height = min(width, source_width), min(height, source_height)
    # 输出尺寸按边长估算，再按 max_side 收敛（保持长宽比，避免拉伸）
    if max(width, height) > max_side:
        factor = max_side / float(max(width, height))
        width, height = max(8, int(width * factor)), max(8, int(height * factor))

    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    warped = cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_LINEAR)
    return warped, matrix


def _warp_min_area_rect(image: np.ndarray, options: PreprocessOptions) -> tuple[np.ndarray | None, np.ndarray, str]:
    """只修旋转、不修透视的兜底方案（轮廓法失败时）。"""
    # 用实心掩膜（Otsu 纸面）而不是细线边缘：细线轮廓的 contourArea 只有线本身那么大
    _, otsu = cv2.threshold(cv2.GaussianBlur(_gray(image), (5, 5), 0), 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, np.eye(3, dtype=np.float64), "no_contour"

    largest = max(contours, key=cv2.contourArea)
    area_ratio = cv2.contourArea(largest) / float(image.shape[0] * image.shape[1])
    if area_ratio < options.min_area_rect_ratio:
        return None, np.eye(3, dtype=np.float64), "area_too_small"

    rect = cv2.minAreaRect(largest)
    box = cv2.boxPoints(rect)
    # 只取"让矩形边水平"的角度：|angle| ≤ 45
    angle = rect[-1]
    if angle > 45:
        angle -= 90
    if angle < -45:
        angle += 90
    return _rotate(image, angle), _rotation_matrix(angle, image.shape[1], image.shape[0]), "min_area_rect"


def _rotation_matrix(angle_deg: float, width: int, height: int) -> np.ndarray:
    """绕中心旋转的 forward 矩阵（图像坐标系，y 向下）。"""
    cx, cy = width / 2.0, height / 2.0
    rad = np.radians(angle_deg)
    cos_a, sin_a = float(np.cos(rad)), float(np.sin(rad))
    return np.array([
        [cos_a, -sin_a, cx - cos_a * cx + sin_a * cy],
        [sin_a, cos_a, cy - sin_a * cx - cos_a * cy],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)


def _rotate(image: np.ndarray, angle_deg: float) -> np.ndarray:
    if abs(angle_deg) < 1e-6:
        return image
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle_deg, 1.0)
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def _deskew(image: np.ndarray, options: PreprocessOptions) -> tuple[np.ndarray, float]:
    """残差小角度：用最小外接矩形的主方向，超过阈值才转。"""
    gray = _gray(image)
    edges = cv2.Canny(gray, 50, 150)
    coordinates = np.column_stack(np.where(edges > 0))
    if coordinates.shape[0] < 50:
        return image, 0.0
    angle = cv2.minAreaRect(coordinates.astype(np.float32).reshape(-1, 1, 2))[-1]
    if angle > 45:
        angle -= 90
    if angle < -45:
        angle += 90
    if abs(angle) < options.deskew_min_deg:
        return image, 0.0
    return _rotate(image, angle), angle


def _compose(previous: np.ndarray, forward: np.ndarray) -> np.ndarray:
    """把"当前图 → 下一步图"的 forward 矩阵合成进"当前图 → 原图"的累计矩阵。"""
    try:
        inverse = np.linalg.inv(forward)
    except np.linalg.LinAlgError:
        return previous
    composed = previous @ inverse
    return composed / composed[2, 2] if abs(composed[2, 2]) > 1e-12 else composed


# ---------------------------------------------------------------------------
# 光度与叠加物
# ---------------------------------------------------------------------------
def _normalize_illumination(image: np.ndarray) -> np.ndarray:
    """大核闭运算估背景 → 除法归一化。只拉平阴影/渐变，不做伽马拉伸（避免洗掉小字）。

    目标亮度必须取**高分位**（p90）而不是中位数：
    票据常被拍在深色桌面上，或者透视校正把一部分桌面带了进来，此时背景的中位数会落在
    "桌面"那一档；用中位数做目标会把整张图压暗、对比度直接压没（实测 contrast 84 → 9.5）。
    p90 近似"纸面亮度"，才是应该对齐的基准。
    """
    gray = _gray(image)
    height, width = gray.shape[:2]
    # 形态学在大图上极贵（实测 2800px 的图光这一步就 500ms+）。
    # 背景是低频信号，在 1/4 尺度估计再插值回来完全够用，精度几乎无损。
    scale = 0.25 if max(height, width) > 1200 else 1.0
    work = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else gray
    longest = max(work.shape[:2])
    kernel_size = max(9, int(longest // 40) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    small_bg = cv2.morphologyEx(work, cv2.MORPH_CLOSE, kernel)
    small_bg = cv2.GaussianBlur(small_bg, (0, 0), sigmaX=max(3, kernel_size / 4))
    background = cv2.resize(small_bg, (width, height), interpolation=cv2.INTER_LINEAR) if scale < 1.0 else small_bg
    background = np.maximum(background, 1).astype(np.float32)

    low, high = np.percentile(background, [10, 90])
    if high / max(low, 1.0) < 1.6:
        return image  # 已经够均匀，别动它（避免过度处理损伤小字）

    # 增益限幅 + 只施加 70%：校正目的是"拉平阴影"，不是"把暗处提亮成白纸"。
    # 实测过冲会把小票的浅灰底提成一片白，反而让 OCR 认错字（seller_name 100%→67%）。
    gain = np.clip(float(high) / background, 0.75, 1.6)
    gain = 1.0 + 0.7 * (gain - 1.0)
    return np.clip(image.astype(np.float32) * gain[..., None], 0, 255).astype(np.uint8)


def _detect_seal(image: np.ndarray, options: PreprocessOptions) -> tuple[list[tuple[int, int, int, int]], float]:
    """HSV 双区间（红/蓝）定位印章。**只标记，不 inpaint**（修补会啃掉笔画）。"""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, (0, 70, 50), (10, 255, 255)) | cv2.inRange(hsv, (170, 70, 50), (180, 255, 255))
    blue = cv2.inRange(hsv, (100, 80, 50), (130, 255, 255))
    mask = cv2.morphologyEx(red | blue, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    total_area = float(image.shape[0] * image.shape[1])
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes: list[tuple[int, int, int, int]] = []
    covered = 0.0
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if area / total_area < options.seal_min_area_ratio:
            continue
        if w < 12 or h < 12:
            continue
        boxes.append((int(x), int(y), int(x + w), int(y + h)))
        covered += area
    return boxes, covered / total_area


def _assess_quality(gray: np.ndarray, seal_ratio: float, options: PreprocessOptions) -> QualityMetrics:
    brightness = float(gray.mean())
    contrast = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    saturated = float((gray >= 250).mean())

    reasons: list[str] = []
    if brightness < options.low_brightness:
        reasons.append("too_dark")
    # 过曝不能看均值：正常白底文件的均值本来就有 235-245，用"饱和像素占比"才判得准
    if saturated > options.overexposed_ratio:
        reasons.append("overexposed")
    if contrast < options.low_contrast:
        reasons.append("low_contrast")
    if sharpness < options.low_sharpness:
        reasons.append("blurry")
    return QualityMetrics(brightness=brightness, contrast=contrast, sharpness=sharpness,
                          seal_ratio=seal_ratio, saturated_ratio=saturated, reasons=reasons)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def preprocess(source: str | Path | bytes | np.ndarray, options: PreprocessOptions | None = None) -> PreprocessResult:
    options = options or PreprocessOptions()
    timings: dict[str, float] = {}
    steps: dict[str, bool] = {}
    warnings: list[str] = []

    started = time.perf_counter()
    image, load_warnings = _load(source, options)
    warnings.extend(load_warnings)
    steps["exif"] = "exif_rotated" in load_warnings or not options.exif
    original_h, original_w = image.shape[:2]
    original_size = (original_w, original_h)
    original_bgr = image  # 重采样用原图：先缩放再裁切会把字缩小，OCR 直接读不出
    # 累计矩阵：当前图坐标 → 原图坐标
    to_original = np.eye(3, dtype=np.float64)

    # 1) 缩放
    scale = 1.0
    if options.resize:
        image, scale, resized = _resize_max_side(image, options.max_side)
        steps["resize"] = resized
        if scale != 1.0:
            to_original = np.diag([1.0 / scale, 1.0 / scale, 1.0])
    timings["load"] = (time.perf_counter() - started) * 1000

    # 2) 去噪（彩色，轻量：保边为主，不糊字）
    if options.denoise:
        image = cv2.bilateralFilter(image, d=5, sigmaColor=50, sigmaSpace=50)
        steps["denoise"] = True
    else:
        steps["denoise"] = False

    # 3) 票据定位 + 透视（降级链：轮廓 → minAreaRect → 原图）
    quad_in_original: list[list[float]] | None = None
    rectify_failed = False
    rotate_deg = 0.0
    if options.rectify:
        step_started = time.perf_counter()
        # 定位不需要高分辨率：纸面边界是低频信号，1000px 足够，且比 1600px 快 2 倍以上
        detect_image, detect_scale = image, 1.0
        if max(image.shape[:2]) > 1000:
            detect_scale = 1000.0 / max(image.shape[:2])
            detect_image = cv2.resize(image, None, fx=detect_scale, fy=detect_scale,
                                      interpolation=cv2.INTER_AREA)
        quad, notes = _find_quad(detect_image, options)
        if quad is not None:
            quad = quad / detect_scale

        # 重采样源图：先压到 warp_max_side 再 warp。直接在 2800px 的原图上做透视/旋转，
        # 光重采样就要几百毫秒，而输出上限本来就只有 warp_max_side，多出来的分辨率是白花的。
        warp_source = original_bgr
        source_scale = 1.0
        longest = max(original_bgr.shape[:2])
        if longest > options.warp_max_side:
            source_scale = options.warp_max_side / float(longest)
            warp_source = cv2.resize(original_bgr, None, fx=source_scale, fy=source_scale,
                                     interpolation=cv2.INTER_AREA)
        source_to_original = np.diag([1.0 / source_scale, 1.0 / source_scale, 1.0])

        if quad is not None:
            # 四角点此刻位于"缩放后图"的坐标系，用当前累计矩阵（缩放）映回原图
            quad_original = cv2.perspectiveTransform(
                quad.reshape(-1, 1, 2).astype(np.float64), to_original
            ).reshape(-1, 2)
            quad_in_original = [[float(x), float(y)] for x, y in quad_original]

            img_height, img_width = original_bgr.shape[:2]
            xs, ys = quad_original[:, 0], quad_original[:, 1]
            width_ratio = (xs.max() - xs.min()) / max(1.0, float(img_width))
            height_ratio = (ys.max() - ys.min()) / max(1.0, float(img_height))
            margin_x, margin_y = img_width * 0.02, img_height * 0.02
            reasons: list[str] = []
            if (height_ratio > 0.85 and width_ratio < 0.65) or (width_ratio > 0.85 and height_ratio < 0.65):
                reasons.append(f"quad_lopsided:{width_ratio:.2f}x{height_ratio:.2f}")
            if (xs.min() < -margin_x or ys.min() < -margin_y
                    or xs.max() > img_width + margin_x or ys.max() > img_height + margin_y):
                reasons.append("quad_out_of_bounds")
            if reasons:
                warnings.extend(reasons)
                quad = None      # 走 minAreaRect 兜底（只修旋转，不裁内容）

        if quad is not None:
            if _quad_is_frame(quad_original * source_scale, warp_source):
                steps["rectify"] = "quad_identity"
                image = warp_source
                to_original = source_to_original
            else:
                warped, forward = _warp_perspective(warp_source, quad_original * source_scale,
                                                    options.warp_max_side)
                to_original = source_to_original @ np.linalg.inv(forward)
                to_original = to_original / to_original[2, 2]
                image = warped
                steps["rectify"] = "quad_perspective"
        else:
            fallback, forward, reason = _warp_min_area_rect(warp_source, options)
            if fallback is not None:
                to_original = source_to_original @ np.linalg.inv(forward)
                to_original = to_original / to_original[2, 2]
                image = fallback
                steps["rectify"] = "min_area_rect"
                warnings.append(f"rectify_fallback:{reason}")
            else:
                steps["rectify"] = "none"
                rectify_failed = True
                image = warp_source
                to_original = source_to_original
                warnings.append(f"rectify_failed:{reason}")
                warnings.extend(notes[:2])
        timings["rectify"] = (time.perf_counter() - step_started) * 1000
    else:
        steps["rectify"] = "disabled"

    # 3.4) 降级路径可能返回原尺寸（未裁切），这里统一压到 warp_max_side，避免后续步骤超时
    if max(image.shape[:2]) > options.warp_max_side:
        factor = options.warp_max_side / float(max(image.shape[:2]))
        new_w = max(8, int(round(image.shape[1] * factor)))
        new_h = max(8, int(round(image.shape[0] * factor)))
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
        to_original = _compose(to_original,
                               np.array([[factor, 0, 0], [0, factor, 0], [0, 0, 1]], dtype=np.float64))
        warnings.append(f"downscaled:{factor:.2f}")

    # 3.5) 小票放大：短边不足 min_side 时放大（最多 2 倍），否则字太小识别不出来
    # 只在真的做过几何校正后放大：没校正时放大只是把原图吹大，没有信息增益
    if (options.min_side and steps["rectify"] in {"quad_perspective", "min_area_rect"}
            and min(image.shape[:2]) < options.min_side):
        factor = min(options.min_side / float(min(image.shape[:2])), 2.0)
        if factor >= 1.5:
            new_w = int(round(image.shape[1] * factor))
            new_h = int(round(image.shape[0] * factor))
            image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
            to_original = _compose(to_original, np.array([[factor, 0, 0], [0, factor, 0], [0, 0, 1]], dtype=np.float64))
            warnings.append(f"upscaled:{factor:.2f}")
            steps["upscale"] = True
    if "upscale" not in steps:
        steps["upscale"] = False

    # 4) 残差小角度
    # 说明：把阈值降到 1.0°、并让 minAreaRect 路径也去斜，实测会掉 3–5 个点的字段准确率
    # （多一次小角度重采样把细笔画糊掉：buyer_tax_no 86.7%→83.3%、total_amount 76.7%→71.7%）。
    # 用户抽查结论是"残留倾斜但字都看得清"，所以维持 1.5° 且只对透视路径做残差校正。
    if options.rotate and steps["rectify"] == "quad_perspective":
        step_started = time.perf_counter()
        rotated, angle = _deskew(image, options)
        if angle:
            to_original = _compose(to_original, _rotation_matrix(angle, image.shape[1], image.shape[0]))
            image = rotated
            rotate_deg = angle
            steps["rotate"] = True
        else:
            steps["rotate"] = False
        timings["rotate"] = (time.perf_counter() - step_started) * 1000
    else:
        steps["rotate"] = False

    # 5) 照度校正
    if options.illum:
        step_started = time.perf_counter()
        image = _normalize_illumination(image)
        steps["illum"] = True
        timings["illum"] = (time.perf_counter() - step_started) * 1000
    else:
        steps["illum"] = False

    # 6) 印章定位（只标记）
    seal_boxes: list[tuple[int, int, int, int]] = []
    seal_ratio = 0.0
    if options.detect_seal:
        step_started = time.perf_counter()
        seal_boxes, seal_ratio = _detect_seal(image, options)
        # 映射回原图坐标
        inverse = np.linalg.inv(to_original)
        seal_boxes = _map_boxes_back(seal_boxes, inverse)
        steps["detect_seal"] = True
        if seal_ratio > 0.05:
            warnings.append("seal_heavy")
        timings["detect_seal"] = (time.perf_counter() - step_started) * 1000
    else:
        steps["detect_seal"] = False

    # 7) 可选二值化（默认关）
    if options.binarize:
        gray = _gray(image)
        image = cv2.cvtColor(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                   cv2.THRESH_BINARY, 31, 10), cv2.COLOR_GRAY2BGR)
        steps["binarize"] = True
    else:
        steps["binarize"] = False

    quality = _assess_quality(_gray(image), seal_ratio, options)
    reasons = list(quality.reasons)
    if rectify_failed:
        reasons.append("rectify_failed")
    quality.reasons = reasons
    quality_flag = "low" if reasons else "ok"

    if quad_in_original is None:
        quad = None
    else:
        quad = [[round(x, 2), round(y, 2)] for x, y in quad_in_original]

    timings["total"] = (time.perf_counter() - started) * 1000
    return PreprocessResult(
        image=image,
        original_size=original_size,
        quality_flag=quality_flag,
        quality=quality,
        quad=quad,
        rotate_deg=rotate_deg,
        seal_boxes=seal_boxes,
        warnings=warnings,
        steps=steps,
        timings_ms={key: round(value, 2) for key, value in timings.items()},
        to_original=to_original,
        scale=scale,
    )


def _map_boxes_back(boxes: list[tuple[int, int, int, int]], inverse: np.ndarray) -> list[tuple[int, int, int, int]]:
    """把校正图上的框映回原图（取变换后的外接矩形）。"""
    if not boxes:
        return []
    mapped: list[tuple[int, int, int, int]] = []
    for x1, y1, x2, y2 in boxes:
        corners = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, inverse).reshape(-1, 2)
        mapped.append((int(round(projected[:, 0].min())), int(round(projected[:, 1].min())),
                       int(round(projected[:, 0].max())), int(round(projected[:, 1].max()))))
    return mapped