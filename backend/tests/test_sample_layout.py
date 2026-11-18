"""S1-1 版式原语的单元测试。

这些测试的价值：绘制函数返回的 bbox 是**标注的唯一来源**，
它一旦算错，整批数据的标注就全错，而且从图上很难看出来（框偏一点点，人眼几乎无感）。
"""
from __future__ import annotations

from PIL import Image

from invoice_layout import Canvas


def test_text_returns_bbox_inside_canvas() -> None:
    canvas = Canvas(400, 200)
    item = canvas.text((20, 30), "发票代码", 24, label="invoice_code")

    x1, y1, x2, y2 = item.bbox
    assert 0 <= x1 < x2 <= canvas.width
    assert 0 <= y1 < y2 <= canvas.height
    # 24 号字，"发票代码" 的宽度应该显著大于 0，且高度在一个合理区间
    assert (x2 - x1) >= 40
    assert 18 <= (y2 - y1) <= 40


def test_text_bbox_matches_ink_position() -> None:
    """bbox 必须真的框住墨迹：框内有暗像素，远离框的区域没有。"""
    canvas = Canvas(300, 120)
    item = canvas.text((40, 40), "1234.56", 32, label="amount", latin=True)
    gray = canvas.image.convert("L")
    x1, y1, x2, y2 = item.bbox

    inside = [gray.getpixel((x, y)) for x in range(x1, x2) for y in range(y1, y2)]
    assert sum(1 for value in inside if value < 128) > 10, "框内几乎没有暗像素，说明 bbox 算偏了"

    outside = [gray.getpixel((x, y)) for x in range(0, 30) for y in range(0, 30)]
    assert all(value > 240 for value in outside), "框外不应有墨迹"


def test_anchor_centered_text_is_centered() -> None:
    canvas = Canvas(200, 100)
    item = canvas.text((100, 50), "标题", 20, label="title", anchor="mm")
    x1, _, x2, _ = item.bbox
    center = (x1 + x2) / 2
    assert abs(center - 100) <= 2


def test_table_records_every_cell() -> None:
    canvas = Canvas(600, 300)
    drawn = canvas.table((20, 20, 580, 200), ["项目", "金额"], [["办公用品", "¥10.00"], ["打印纸", "¥20.00"]], 18)
    # 2 个表头 + 4 个单元格
    assert len(drawn) == 6
    labels = {item.label for item in drawn}
    assert labels == {"table_header", "table_row"}
    for item in drawn:
        x1, y1, x2, y2 = item.bbox
        assert 0 <= x1 < x2 <= canvas.width and 0 <= y1 < y2 <= canvas.height


def test_qrcode_placeholder_is_recorded_but_not_scannable() -> None:
    """二维码只画占位方块，避免生成可扫描的假发票信息。"""
    canvas = Canvas(300, 300)
    item = canvas.qrcode_placeholder((20, 20, 180, 180))
    assert item.label == "qrcode"
    assert item.bbox == (20, 20, 180, 180)
    assert isinstance(canvas.image, Image.Image)