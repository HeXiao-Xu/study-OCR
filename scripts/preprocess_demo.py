#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预处理批量演示（S1-2）。

产出"左原图 / 右校正图"的对照拼图 + 一份统计汇总，供人工抽查。
开发环境看不了图不算问题——**自动判定在 `preprocess_eval.py`**（OCR 前后对比），
这个脚本的价值是给人和答辩材料留可视证据。

用法：
    python scripts/preprocess_demo.py --limit 12
    python scripts/preprocess_demo.py --dir samples/synth_v1 --limit 0   # 0 = 全部
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ocr.preprocess import PreprocessOptions, preprocess  # noqa: E402

DEFAULT_OUT = ROOT / "backend" / "eval" / "results" / "preprocess"
_PANEL_HEIGHT = 620


def _panel(image: np.ndarray, height: int = _PANEL_HEIGHT) -> np.ndarray:
    scale = height / max(1, image.shape[0])
    return cv2.resize(image, (max(1, int(image.shape[1] * scale)), height), interpolation=cv2.INTER_AREA)


def _label(image: np.ndarray, text: str) -> np.ndarray:
    canvas = image.copy()
    cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 34), (32, 32, 32), -1)
    cv2.putText(canvas, text, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (245, 245, 245), 1, cv2.LINE_AA)
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description="预处理批量演示（S1-2）")
    parser.add_argument("--dir", type=Path, default=ROOT / "samples" / "synth_v1")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=12, help="0 表示全部")
    parser.add_argument("--max-side", type=int, default=1600)
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    images = sorted(p for p in args.dir.glob("*.jpg")) + sorted(p for p in args.dir.glob("*.png"))
    if not images:
        print(f"[FAIL] {args.dir} 里没有图片；先跑 scripts/make_samples.py 生成合成集")
        return 1
    if args.limit:
        images = images[: args.limit]

    args.out.mkdir(parents=True, exist_ok=True)
    options = PreprocessOptions(max_side=args.max_side)
    methods = Counter()
    flags = Counter()
    retake: list[tuple[str, list[str]]] = []
    timings: list[float] = []
    rows: list[dict] = []

    for path in images:
        original = cv2.imread(str(path))
        result = preprocess(path, options)
        methods[str(result.steps["rectify"])] += 1
        flags[result.quality_flag] += 1
        if result.needs_retake:
            retake.append((path.name, result.retake_hints))
        timings.append(result.timings_ms["total"])

        left = _label(_panel(original), f"original {original.shape[1]}x{original.shape[0]}")
        right = _label(
            _panel(result.image),
            f"corrected | {result.steps['rectify']} | q={result.quality_flag} | {result.timings_ms['total']:.0f}ms",
        )
        height = max(left.shape[0], right.shape[0])
        canvas = np.full((height, left.shape[1] + right.shape[1] + 8, 3), 20, dtype=np.uint8)
        canvas[: left.shape[0], : left.shape[1]] = left
        canvas[: right.shape[0], left.shape[1] + 8:] = right
        cv2.imwrite(str(args.out / f"{path.stem}_compare.jpg"), canvas, [cv2.IMWRITE_JPEG_QUALITY, 85])

        rows.append({
            "image": path.name,
            "rectify": result.steps["rectify"],
            "quality": result.quality_flag,
            "reasons": result.quality.reasons,
            "brightness": round(result.quality.brightness, 1),
            "contrast": round(result.quality.contrast, 1),
            "sharpness": round(result.quality.sharpness, 1),
            "seal_boxes": len(result.seal_boxes),
            "elapsed_ms": result.timings_ms["total"],
            "warnings": result.warnings,
        })

    summary = {
        "count": len(images),
        "dir": str(args.dir),
        "out": str(args.out),
        "rectify_methods": dict(methods),
        "quality_flags": dict(flags),
        "needs_retake": [{"image": name, "hints": hints} for name, hints in retake],
        "elapsed_ms": {
            "p50": round(float(np.percentile(timings, 50)), 1),
            "p90": round(float(np.percentile(timings, 90)), 1),
            "p95": round(float(np.percentile(timings, 95)), 1),
            "max": round(float(max(timings)), 1),
        },
        "quad_success_rate": round(
            (methods.get("quad_perspective", 0) + methods.get("quad_identity", 0)) / len(images), 3
        ),
        "rows": rows,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"处理 {len(images)} 张 → {args.out}")
    print(f"  rectify 方式：{dict(methods)}")
    print(f"  quality_flag：{dict(flags)}")
    print(f"  建议重拍：{len(retake)} 张" + (f"（例：{retake[0][0]} → {retake[0][1][0]}）" if retake else ""))
    print(f"  几何校正成功率：{summary['quad_success_rate']:.0%}"
          f"（其中已在正视角 {methods.get('quad_identity', 0)} 张，免重采样）")
    print(f"  耗时 ms：P50={summary['elapsed_ms']['p50']} P95={summary['elapsed_ms']['p95']} "
          f"max={summary['elapsed_ms']['max']}")
    print(f"  对照拼图：{len(images)} 张（{args.out.name}/*_compare.jpg）")
    print(f"  汇总：{(args.out / 'summary.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())