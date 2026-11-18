#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文字体探测（S1-1）。

合成票据必须用**真实中文字体**，否则 Pillow 会画出一堆方框，后面的 OCR 反查验收会直接失败。
这里只做一件事：找到系统里可用的中文字体，并给出统一的取字入口。
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

# 按"横竖都好看、字符全"排序：微软雅黑 → 黑体 → 宋体 → 思源
CANDIDATES: list[tuple[str, int]] = [
    ("msyh.ttc", 0),      # 微软雅黑（Win10/11 自带）
    ("msyhbd.ttc", 0),    # 雅黑粗体
    ("simhei.ttf", 0),    # 黑体
    ("simsun.ttc", 0),    # 宋体
    ("Deng.ttf", 0),      # 等线
    ("NotoSansCJK-Regular.ttc", 0),      # Linux
    ("NotoSansSC-Regular.otf", 0),
    ("wqy-zenhei.ttc", 0),
]

SEARCH_DIRS = [
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts"),
    Path("/usr/share/fonts/truetype"),
    Path("/System/Library/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
]

BOLD_CANDIDATES = ["msyhbd.ttc", "simhei.ttf"]

# 数字与金额用拉丁字体：真实票据的金额/代码多用等宽无衬线体，且 OCR 对拉丁数字更稳
LATIN_CANDIDATES: list[tuple[str, int]] = [
    ("arial.ttf", 0),
    ("arialbd.ttf", 0),
    ("consola.ttf", 0),
    ("DejaVuSans.ttf", 0),
]


class FontNotFoundError(RuntimeError):
    pass


def _iter_font_files() -> list[Path]:
    found: list[Path] = []
    for directory in SEARCH_DIRS:
        if not directory.exists():
            continue
        for name, _index in CANDIDATES:
            candidate = directory / name
            if candidate.exists():
                found.append(candidate)
        # 目录里直接扫一遍，覆盖没列进 CANDIDATES 的
        try:
            found.extend(p for p in directory.rglob("*.tt*") if p not in found)
        except OSError:
            pass
    return found


def find_font_path(bold: bool = False) -> tuple[Path, int]:
    """返回 (字体文件, TTC 索引)。找不到就抛 FontNotFoundError（启动即失败，不静默降级）。"""
    files = _iter_font_files()
    by_name = {p.name.lower(): p for p in files}

    preferred = BOLD_CANDIDATES if bold else [n for n, _ in CANDIDATES]
    for name in preferred:
        path = by_name.get(name.lower())
        if path:
            return path, 0
    for name, index in CANDIDATES:
        for path in files:
            if path.name.lower().endswith((".ttc", ".ttf", ".otf")):
                # 逐字体试着加载 12 号中文，能画出来才算可用
                try:
                    font = ImageFont.truetype(str(path), 12, index=index)
                    font.getbbox("发票")
                    return path, index
                except Exception:  # noqa: BLE001 - 字体损坏/不支持中文都跳过
                    continue
    raise FontNotFoundError(
        "找不到可用的中文字体。Windows 一般自带 C:/Windows/Fonts/msyh.ttc；\n"
        "Linux 可安装：apt install fonts-wqy-zenhei（或 fonts-noto-cjk）。"
    )


def find_latin_font_path(bold: bool = False) -> tuple[Path, int] | None:
    files = _iter_font_files()
    by_name = {p.name.lower(): p for p in files}
    order = (["arialbd.ttf", "consola.ttf"] if bold else []) + [name for name, _ in LATIN_CANDIDATES]
    for name in order:
        path = by_name.get(name.lower())
        if path:
            return path, 0
    return None


@lru_cache(maxsize=128)
def load_font(size: int, bold: bool = False, latin: bool = False) -> ImageFont.FreeTypeFont:
    if latin:
        found = find_latin_font_path(bold=bold)
        if found:
            path, index = found
            return ImageFont.truetype(str(path), size, index=index)
        # 没有拉丁字体就退回中文字体（数字也能画，只是字形略宽）
    path, index = find_font_path(bold=bold)
    return ImageFont.truetype(str(path), size, index=index)


def describe() -> str:
    try:
        regular, _ = find_font_path(bold=False)
        bold_path, _ = find_font_path(bold=True)
        return f"常规={regular.name}  粗体={bold_path.name}"
    except FontNotFoundError as exc:
        return f"未找到中文字体：{exc}"


if __name__ == "__main__":
    try:
        print(describe())
    except Exception as exc:  # noqa: BLE001
        print(exc, file=sys.stderr)
        sys.exit(2)