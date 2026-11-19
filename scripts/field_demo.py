#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字段抽取演示（S1-5）。

一条命令看完整识别链路：预处理 → OCR → 票种/区域 → 字段抽取 → 校验。

用法：
    python scripts/field_demo.py --image samples/synth_v1/vat_digital_landscape_001.jpg
    python scripts/field_demo.py --dir samples/synth_v1 --limit 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ocr import get_engine, preprocess  # noqa: E402
from app.ocr.extractor import extract_fields  # noqa: E402
from app.ocr.layout import analyze_layout  # noqa: E402

DEFAULT_OUT = ROOT / "backend" / "eval" / "results" / "fields"


def run_one(path: Path, engine, use_preprocess: bool = True) -> dict:
    if use_preprocess:
        pp = preprocess(path)
        image = pp.image
    else:
        pp = None
        image = cv2.imread(str(path))
        if image is None:
            raise FileNotFoundError(f"读不到图片：{path}")

    ocr = engine.recognize(image)
    layout = analyze_layout(ocr.lines, (image.shape[1], image.shape[0]))
    result = extract_fields(ocr.lines, (image.shape[1], image.shape[0]), layout.ticket_type, layout)

    print(f"\n=== {path.name} ===")
    if pp is not None:
        print(f"预处理：{pp.steps['rectify']} | quality={pp.flag if hasattr(pp, 'flag') else pp.quality_flag}"
              f" | {pp.timings_ms['total']:.0f}ms")
    print(f"票种：{layout.ticket_type}（score={layout.classification.score:.2f}）"
          f" ｜ OCR {len(ocr.lines)} 行 ｜ 推理 {ocr.elapsed_ms:.0f}ms")
    print(f"{'字段':<20}{'值':<30}{'置信度':>8}{'行号':>6}  原始文本")
    print("-" * 104)
    for name in sorted(result.fields):
        field = result.fields[name]
        print(f"{name:<20}{field.value:<30}{field.confidence:>8.3f}"
              f"{str(field.source_line_no):>6}  {field.raw_text[:36]}")

    if result.missing_required:
        print(f"缺失必填：{result.missing_required}")
    if result.conflicts:
        print("冲突（未自动替换，需人工确认）：")
        for conflict in result.conflicts:
            values = [candidate["value"] for candidate in conflict["candidates"]]
            print(f"  {conflict['field']}: {values}")
    if result.warnings:
        print(f"告警：{result.warnings}")
    return result.model_dump()


def main() -> int:
    parser = argparse.ArgumentParser(description="字段抽取演示（S1-5）")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-preprocess", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    if not args.image and not args.dir:
        parser.error("需要 --image 或 --dir")

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    engine = get_engine()
    records: list[dict] = []
    images = [args.image] if args.image else sorted(args.dir.glob("*.jpg")) + sorted(args.dir.glob("*.png"))
    if args.image:
        images = [args.image]
    elif args.limit:
        images = images[: args.limit]

    for path in images:
        records.append(run_one(path, engine, not args.no_preprocess))

    out = args.out or (DEFAULT_OUT / "fields_sample.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n结果：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())