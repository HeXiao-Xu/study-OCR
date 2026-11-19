#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCR 最小闭环演示（S1-3）。

打印"带坐标与置信度的文本行"，并对批量目录给出统计（行数、置信度、耗时）。
这是 S1-3 的验收工具，也是 S1-4/S1-5 调参时看"引擎到底看到了什么"的入口。

用法：
    python scripts/ocr_demo.py --image samples/synth_v1/vat_digital_landscape_001.jpg
    python scripts/ocr_demo.py --image <file> --preprocess        # 先跑 S1-2 预处理
    python scripts/ocr_demo.py --dir samples/synth_v1 --limit 20  # 批量统计
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from statistics import median

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ocr import OcrOptions, get_engine, preprocess  # noqa: E402

_CJK = re.compile(r"[\u4e00-\u9fa5]")


def _load(path: Path, use_preprocess: bool):
    if not use_preprocess:
        import cv2

        image = cv2.imread(str(path))
        if image is None:
            raise FileNotFoundError(f"读不到图片：{path}")
        return image, None
    result = preprocess(path)
    return result.image, result


def _print_result(path: Path, engine, image, pp_result) -> dict:
    started = time.perf_counter()
    result = engine.recognize(image)
    wall_ms = (time.perf_counter() - started) * 1000

    print(f"\n=== {path.name} ===")
    if pp_result is not None:
        print(f"预处理：{pp_result.steps['rectify']} | quality={pp_result.quality_flag} | "
              f"{pp_result.timings_ms['total']:.0f}ms")
    print(f"图像：{image.shape[1]}x{image.shape[0]} ｜ 行数：{len(result.lines)} ｜ "
          f"平均置信度：{result.mean_score:.3f} ｜ 推理：{result.elapsed_ms:.0f}ms（含调度 {wall_ms:.0f}ms）")

    for line in result.lines:
        x1, y1, x2, y2 = line.bbox()
        print(f"  #{line.line_no:02d} score={line.score:.4f} "
              f"y={line.y_center:6.1f} box=[{x1},{y1},{x2},{y2}] {line.text}")

    text = "".join(line.text for line in result.lines)
    cjk_ratio = len(_CJK.findall(text)) / max(1, len(text))
    garbled = "\ufffd" in text
    print(f"中文占比：{cjk_ratio:.0%} ｜ 乱码字符：{'有' if garbled else '无'}")
    return {
        "image": path.name,
        "size": [image.shape[1], image.shape[0]],
        "lines": len(result.lines),
        "mean_score": round(result.mean_score, 4),
        "cjk_ratio": round(cjk_ratio, 3),
        "garbled": garbled,
        "elapsed_ms": result.elapsed_ms,
        "texts": [line.text for line in result.lines],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR 最小闭环（S1-3）")
    parser.add_argument("--image", type=Path, help="单张图片")
    parser.add_argument("--dir", type=Path, help="批量目录")
    parser.add_argument("--limit", type=int, default=0, help="批量时限制张数（0=全部）")
    parser.add_argument("--preprocess", action="store_true", help="先跑 S1-2 预处理")
    parser.add_argument("--check-determinism", action="store_true", help="同一张图跑两次比对（单张时默认开）")
    parser.add_argument("--out", type=Path, default=None, help="把结果 JSON 写到文件")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if not args.image and not args.dir:
        parser.error("需要 --image 或 --dir")

    options = OcrOptions.from_settings(_settings())
    engine = get_engine(options)
    print(f"引擎：paddleocr {engine.version} ｜ 参数：{options.as_det_params()}")

    init_started = time.perf_counter()
    engine._ensure()  # noqa: SLF001 - 显式预热，把"模型加载 10s"和"推理 2s"分开计时
    print(f"模型加载：{time.perf_counter() - init_started:.1f}s")

    records: list[dict] = []

    if args.image:
        image, pp_result = _load(args.image, args.preprocess)
        record = _print_result(args.image, engine, image, pp_result)
        records.append(record)

        # 确定性：同一输入两次结果必须完全一致（S1-5 靠行号回查）
        if args.check_determinism or not args.dir:
            again = engine.recognize(image)
            same = [(l.text, round(l.score, 6), l.line_no) for l in again.lines] == \
                   [(l.text, round(l.score, 6), l.line_no) for l in engine.recognize(image).lines]
            print(f"确定性（连跑两次一致）：{'是' if same else '否'}")
            record["deterministic"] = same

    if args.dir:
        images = sorted(args.dir.glob("*.jpg")) + sorted(args.dir.glob("*.png"))
        if args.limit:
            images = images[: args.limit]
        rows = []
        for path in images:
            image, pp_result = _load(path, args.preprocess)
            result = engine.recognize(image)
            text = "".join(line.text for line in result.lines)
            rows.append({
                "image": path.name,
                "lines": len(result.lines),
                "mean_score": result.mean_score,
                "cjk_ratio": len(_CJK.findall(text)) / max(1, len(text)),
                "elapsed_ms": result.elapsed_ms,
                "rectify": pp_result.steps["rectify"] if pp_result else "",
            })
        records.extend(rows)
        line_counts = [row["lines"] for row in rows]
        scores = [row["mean_score"] for row in rows]
        elapsed = [row["elapsed_ms"] for row in rows]
        print(f"\n=== 批量统计（{len(rows)} 张{'，含预处理' if args.preprocess else ''}）===")
        print(f"行数：均值 {np.mean(line_counts):.1f} ｜ 最少 {min(line_counts)} ｜ 最多 {max(line_counts)}")
        print(f"平均置信度：{np.mean(scores):.3f}")
        print(f"推理耗时 ms：P50 {np.percentile(elapsed, 50):.0f} ｜ P95 {np.percentile(elapsed, 95):.0f} "
              f"｜ max {max(elapsed):.0f}")
        negatives = [row["image"] for row in rows if row["lines"] == 0]
        if negatives:
            print(f"[WARN] 有 {len(negatives)} 张没识别出任何行：{negatives[:3]}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    return 0


def _settings():
    """从 backend/.env 读配置（与线上一致）；读不到就用默认值。"""
    try:
        from app.core.config import Settings

        # 显式指定 env 文件：脚本可能从仓库根运行，而 Settings 默认按 CWD 找 .env
        return Settings(_env_file=ROOT / "backend" / ".env")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 读取 backend/.env 失败（{str(exc).splitlines()[0]}），使用默认参数")
        return object()


if __name__ == "__main__":
    sys.exit(main())