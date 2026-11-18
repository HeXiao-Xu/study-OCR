#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版式原语：一切绘制都**返回坐标记录**（S1-1）。

为什么这么做：标注框的正确性不能靠"画完再估"。这里每个绘制方法都用
`ImageDraw.textbbox`（Pillow 按真实字形计算）拿到精确外接框，
再随文本一起返回。生成器据此直接产出标注，做到"像素与标注同源"。

坐标约定：`bbox = (x1, y1, x2, y2)`，左上-右下，原图坐标系（与 AGENTS §5 一致）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from sample_fonts import load_font

RGB = tuple[int, int, int]
Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Drawn:
    """一处已绘制内容及其精确外接框。label 为字段名或版式标签。"""

    label: str
    text: str
    bbox: Box

    def as_dict(self, line_no: int = -1) -> dict:
        item = {"label": self.label, "text": self.text, "bbox": list(self.bbox)}
        if line_no >= 0:
            item["line_no"] = line_no
        return item


@dataclass
class Canvas:
    width: int
    height: int
    background: RGB = (255, 255, 255)
    drawn: list[Drawn] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.image = Image.new("RGB", (self.width, self.height), self.background)
        self.draw = ImageDraw.Draw(self.image)

    # ---------------- 文本 ----------------
    def text(
        self,
        xy: tuple[float, float],
        content: str,
        size: int,
        label: str = "text",
        *,
        bold: bool = False,
        fill: RGB = (0, 0, 0),
        anchor: str | None = None,
        latin: bool = False,
    ) -> Drawn:
        font = load_font(size, bold=bold, latin=latin)
        bbox = self.draw.textbbox(xy, content, font=font, anchor=anchor)
        self.draw.text(xy, content, font=font, fill=fill, anchor=anchor)
        item = Drawn(label=label, text=content, bbox=(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])))
        self.drawn.append(item)
        return item

    def text_right(
        self, right_x: float, y: float, content: str, size: int, label: str = "text", **kwargs
    ) -> Drawn:
        """右对齐到 right_x（金额列用得上）。"""
        font = load_font(size, bold=kwargs.get("bold", False), latin=kwargs.get("latin", False))
        width = font.getbbox(content)[2] - font.getbbox(content)[0]
        return self.text((right_x - width, y), content, size, label, **kwargs)

    # ---------------- 线与矩形 ----------------
    def line(self, start: tuple[float, float], end: tuple[float, float], *, width: int = 1, fill: RGB = (60, 60, 60)) -> None:
        self.draw.line([start, end], fill=fill, width=width)

    def rect(self, box: Box, *, width: int = 1, outline: RGB = (60, 60, 60), fill: RGB | None = None) -> None:
        self.draw.rectangle(list(box), outline=outline, width=width, fill=fill)

    # ---------------- 表格 ----------------
    def table(
        self,
        box: Box,
        headers: list[str],
        rows: list[list[str]],
        size: int,
        *,
        row_height: int = 40,
        label_rows: str = "table_row",
        label_headers: str = "table_header",
    ) -> list[Drawn]:
        """画一个规则表格：表头 + 数据行，返回每个单元格的文本记录。"""
        x1, y1, x2, y2 = box
        col_count = len(headers)
        col_width = (x2 - x1) / col_count

        self.rect(box, width=2, outline=(90, 90, 90))
        out: list[Drawn] = []
        for col, title in enumerate(headers):
            cx = x1 + col * col_width + 8
            out.append(self.text((cx, y1 + 8), title, size, label_headers))
        self.line((x1, y1 + row_height), (x2, y1 + row_height), width=1)

        for row_index, row in enumerate(rows):
            row_top = y1 + row_height * (row_index + 1)
            for col, cell in enumerate(row):
                cx = x1 + col * col_width + 8
                out.append(self.text((cx, row_top + 8), cell, size, label_rows))
            if row_index < len(rows) - 1:
                self.line((x1, row_top + row_height), (x2, row_top + row_height), width=1)

        for col in range(1, col_count):
            vx = x1 + col * col_width
            self.line((vx, y1), (vx, min(y2, y1 + row_height * (len(rows) + 1))), width=1)
        return out

    # ---------------- 印章 / 二维码 ----------------
    def seal(self, center: tuple[int, int], radius: int, text: str = "发票专用章") -> Drawn:
        """半透明红色圆章：中心两行小字 + 外圈。真实票据的章会压在文字上，这里模拟遮挡。"""
        overlay = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        cx, cy = center
        odraw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=(200, 30, 40, 170),
            width=max(3, radius // 12),
        )
        odraw.ellipse(
            [cx - radius * 0.78, cy - radius * 0.78, cx + radius * 0.78, cy + radius * 0.78],
            outline=(200, 30, 40, 110),
            width=max(1, radius // 40),
        )
        font = load_font(max(10, radius // 5))
        odraw.text((cx, cy), text, font=font, fill=(200, 30, 40, 190), anchor="mm")
        self.image = Image.alpha_composite(self.image.convert("RGBA"), overlay).convert("RGB")
        self.draw = ImageDraw.Draw(self.image)

        # 章本身不参与字段抽取，但标注出来有助于后续"遮挡区域"分析
        bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
        item = Drawn(label="seal_zone", text=text, bbox=bbox)
        self.drawn.append(item)
        return item

    def qrcode_placeholder(self, box: Box) -> Drawn:
        """只画占位方块，绝不生成可扫描的二维码（避免造出可用的假发票号）。"""
        x1, y1, x2, y2 = box
        self.rect(box, width=2, outline=(0, 0, 0))
        cell = (x2 - x1) // 8
        for row in range(8):
            for col in range(8):
                if (row * 7 + col * 3) % 4 == 0:
                    self.draw.rectangle(
                        [x1 + col * cell, y1 + row * cell, x1 + (col + 1) * cell - 1, y1 + (row + 1) * cell - 1],
                        fill=(0, 0, 0),
                    )
        item = Drawn(label="qrcode", text="[QR]", bbox=box)
        self.drawn.append(item)
        return item

    def border(self, margin: int = 12, width: int = 2) -> None:
        self.rect((margin, margin, self.width - margin, self.height - margin), width=width, outline=(120, 60, 60))