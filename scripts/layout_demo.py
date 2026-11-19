#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版面分析演示（S1-4）：OCR → 判票种 → 分区域 → 输出 debug 图。

debug 图把三件事画在一张图上，方便人工抽查（也直接可做答辩材料）：
- 区域带：header / body / footer 三条归一化区域的边界线；
- 行框：每行四点框，按所属区域着色；
- 行标签：行号 + 区域首字母。

用法：
    python scripts/layout_demo.py --image samples/synth_v1/vat_digital_landscape_001.jpg
    python scripts/layout_demo.py --dir samples/synth_v1 --limit 10 --preprocess
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ocr import get_engine, preprocess  # noqa: E402
from app.ocr.layout import analyze_layout, load_templates  # noqa: E402
from app.ocr.types import OcrLine  # noqa: E402

DEFAULT_OUT = ROOT / "backend" / "eval" / "results" / "layout"
_REGION_COLORS = {"header": (60, 160, 255), "body": (80, 200, 120), "footer": (200, 120, 240)}


def _draw_debug(image: np.ndarray, lines: list[OcrLine], layout, regions: dict[str, list[float]],
                title: str) -> np.ndarray:
    canvas = image.copy()
    height, width = canvas.shape[:2]

    # 归一化区域 → 像素矩形（画横带边界）
    for name, rect in regions.items():
        x1, y1, x2, y2 = rect
        color = _REGION_COLORS.get(name, (180, 180, 180))
        cv2.rectangle(canvas, (int(x1 * width), int(y1 * height)), (int(x2 * width), int(y2 * height)), color, 2)
        cv2.putText(canvas, name, (int(x1 * width) + 8, int(y1 * height) + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)

    for line in lines:
        region = layout.regions.line_regions.get(line.line_no, "body")
        color = _REGION_COLORS.get(region, (128, 128, 128))
        points = np.array(line.box, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [points], isClosed=True, color=color, thickness=2)
        x, y = int(line.x_left), int(line.y_center)
        cv2.putText(canvas, f"{line.line_no}:{region[0]}", (x, max(12, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)

    banner = f"{title} | type={layout.ticket_type} score={layout.classification.score:.2f}"
    cv2.rectangle(canvas, (0, 0), (width, 30), (24, 24, 24), -1)
    cv2.putText(canvas, banner, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (240, 240, 240), 1, cv2.LINE_AA)
    return canvas


def _regions_for(layout, image: np.ndarray) -> dict[str, list[float]]:
    templates = load_templates()
    if layout.ticket_type in templates:
        aspect = image.shape[1] / max(1, image.shape[0])
        return templates[layout.ticket_type].regions_for(aspect)
    return {"header": [0.0, 0.0, 1.0, 0.0], "body": [0.0, 0.0, 1.0, 1.0], "footer": [0.0, 1.0, 1.0, 1.0]}


def run_one(path: Path, engine, use_preprocess: bool, out_dir: Path | None) -> dict:
    if use_preprocess:
        pp = preprocess(path)
        image = pp.image
    else:
        pp = None
        image = cv2.imread(str(path))
        if image is None:
            raise FileNotFoundError(f"读不到图片：{path}")

    result = engine.recognize(image)
    layout = analyze_layout(result.lines, (image.shape[1], image.shape[0]))

    print(f"\n=== {path.name} ===")
    if pp is not None:
        print(f"预处理：{pp.steps['rectify']} | quality={pp.quality_flag}")
    print(f"票种：{layout.ticket_type}（score={layout.classification.score:.2f}）"
          f" 命中关键字：{layout.classification.matched[:4]}")
    print(f"区域行数：{layout.regions.region_counts} ｜ blocks：{len(layout.regions.blocks)}")
    if layout.classification.warnings or layout.regions.warnings:
        print(f"告警：{layout.classification.warnings + layout.regions.warnings}")
    for block in layout.regions.blocks[:6]:
        first_line = block.text.splitlines()[0] if block.text else ""
        print(f"  block#{block.block_id:02d} [{block.region}] {len(block.line_nos)} 行 "
              f"bbox={block.bbox} 「{first_line[:24]}」")

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        debug = _draw_debug(image, result.lines, layout, _regions_for(layout, image), path.stem)
        target = out_dir / f"{path.stem}_layout.jpg"
        cv2.imwrite(str(target), debug, [cv2.IMWRITE_JPEG_QUALITY, 85])
        print(f"  debug 图：{target.relative_to(ROOT)}")

    return {
        "image": path.name,
        "ticket_type": layout.ticket_type,
        "score": layout.classification.score,
        "matched": layout.classification.matched,
        "region_counts": layout.regions.region_counts,
        "blocks": len(layout.regions.blocks),
        "warnings": layout.classification.warnings + layout.regions.warnings,
        "lines": len(result.lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="版面分析演示（S1-4）")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    # 默认先过 S1-2 预处理：区域是按**纸面**归一化的，带桌面背景的原图会让 header/footer 落空
    parser.add_argument("--no-preprocess", action="store_true", help="不预处理（对照用）")
    parser.add_argument("--no-debug", action="store_true", help="不输出 debug 图")
    parser.add_argument("--out", type=Path, default=None, help="汇总 JSON")
    args = parser.parse_args()
    if not args.image and not args.dir:
        parser.error("需要 --image 或 --dir")

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    engine = get_engine()
    out_dir = None if args.no_debug else DEFAULT_OUT
    records: list[dict] = []

    use_preprocess = not args.no_preprocess
    if args.image:
        records.append(run_one(args.image, engine, use_preprocess, out_dir))
    if args.dir:
        images = sorted(args.dir.glob("*.jpg")) + sorted(args.dir.glob("*.png"))
        if args.limit:
            images = images[: args.limit]
        for path in images:
            records.append(run_one(path, engine, use_preprocess, out_dir))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n汇总：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())