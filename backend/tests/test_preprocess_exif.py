"""S1-2 预处理：EXIF 方向与边界情况。

EXIF 是"第一条不能错的步骤"：手机竖拍的图不解 EXIF 就会躺倒，后面检测、抽取全废。
这类错误从指标上看不出来（只是"识别率低"），所以必须用单测钉死。
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from app.ocr.preprocess import PreprocessOptions, preprocess


def _save_with_orientation(path, size, orientation: int) -> None:
    image = Image.new("RGB", size, (255, 255, 255))
    # 画一个不对称的标记，便于确认方向确实变了
    for x in range(size[0] // 4):
        for y in range(size[1] // 8):
            image.putpixel((x, y), (0, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = orientation
    image.save(path, format="JPEG", exif=exif, quality=92)


def test_exif_orientation_6_rotates_to_landscape(tmp_path) -> None:
    path = tmp_path / "portrait.jpg"
    _save_with_orientation(path, (200, 400), orientation=6)

    result = preprocess(path, PreprocessOptions(rectify=False, illum=False, detect_seal=False))
    # Orientation=6 表示"顺时针转 90° 才是正的"，转完后 200×400 → 400×200
    assert result.original_size == (400, 200), result.original_size
    assert result.image.shape[1] > result.image.shape[0]
    assert "exif_rotated" in result.warnings
    assert result.steps["exif"] is True


def test_exif_disabled_keeps_original_orientation(tmp_path) -> None:
    path = tmp_path / "portrait2.jpg"
    _save_with_orientation(path, (200, 400), orientation=6)

    result = preprocess(path, PreprocessOptions(exif=False, rectify=False, illum=False, detect_seal=False))
    assert result.original_size == (200, 400)
    assert result.image.shape[0] > result.image.shape[1]


def test_tiny_image_is_not_upscaled(tmp_path) -> None:
    path = tmp_path / "tiny.jpg"
    Image.new("RGB", (80, 60), (250, 250, 250)).save(path, quality=90)

    result = preprocess(path, PreprocessOptions(rectify=False, illum=False, detect_seal=False))
    assert result.scale == 1.0
    assert result.image.shape[:2] == (60, 80)


def test_large_image_is_downscaled_to_max_side(tmp_path) -> None:
    path = tmp_path / "big.jpg"
    Image.new("RGB", (2400, 1200), (250, 250, 250)).save(path, quality=90)

    result = preprocess(path, PreprocessOptions(max_side=1000, rectify=False, illum=False, detect_seal=False))
    assert max(result.image.shape[:2]) == 1000
    assert 0 < result.scale < 1


def test_missing_file_raises_readable_error(tmp_path) -> None:
    try:
        preprocess(tmp_path / "nope.jpg")
    except FileNotFoundError as exc:
        assert "不存在" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("文件不存在时应当抛 FileNotFoundError")


def test_no_structure_image_degrades_without_raising() -> None:
    """纯噪声图（无票据边界）：必须走降级链、打 low、不抛异常。"""
    rng = np.random.default_rng(7)
    # 低对比噪声：既没有票据轮廓，又满足"低对比度"的质量判定
    noise = rng.integers(120, 136, size=(400, 300, 3), dtype=np.uint8)
    result = preprocess(noise, PreprocessOptions(illum=False))

    assert result.quad is None
    assert result.steps["rectify"] in {"none", "min_area_rect"}
    assert result.quality_flag == "low"
    assert any("rectify" in warning for warning in result.warnings)
    assert result.image.shape[0] > 0 and result.image.shape[1] > 0