#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预处理增益评估（S1-2 最硬的一条验收）。

做法：同一批样本，**预处理前后各跑一次系统 OCR**，比较字段命中率。
意义：照度校正/透视校正很容易"看起来更干净，OCR 反而更差"——
用这个脚本把"预处理到底有没有帮助"变成可判定的数字，而不是靠感觉。

判定：平均字段命中率**不允许下降**（下降 > 2 个百分点即失败），
单张下降只记录、不判失败（个别难样本可能因为重采样变模糊）。

用法：
    python scripts/preprocess_eval.py --n 8
    python scripts/preprocess_eval.py --n 12 --split hard
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app.ocr.preprocess import PreprocessOptions, preprocess  # noqa: E402
from verify_samples import field_hit_rate, image_stats, load_records, ocr_text  # noqa: E402

DEFAULT_ANN = ROOT / "backend" / "eval" / "datasets" / "annotations_synth_v1.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description="预处理前后 OCR 对比（S1-2）")
    parser.add_argument("--ann", type=Path, default=DEFAULT_ANN)
    parser.add_argument("--n", type=int, default=8, help="抽样张数（难度与干净各半）")
    parser.add_argument("--split", choices=["all", "hard", "clean"], default="all")
    parser.add_argument("--max-side", type=int, default=1600)
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    records = load_records(args.ann)
    # 难度排序：灰度均值越低越难
    ranked = sorted(records, key=lambda r: image_stats(r)[0])
    if args.split == "hard":
        picked = ranked[: args.n]
    elif args.split == "clean":
        picked = ranked[-args.n:]
    else:
        half = args.n // 2
        picked = ranked[:half] + ranked[-(args.n - half):]

    options = PreprocessOptions(max_side=args.max_side)
    before_rates, after_rates = [], []
    print(f"{'样本':<44}{'原图 OCR':>10}{'预处理后':>10}{'差':>8}")
    print("-" * 74)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for record in picked:
            path = ROOT / record["image"]
            name = Path(record["image"]).name

            text_before, err_before = ocr_text(path)
            if err_before:
                print(f"[FAIL] OCR 不可用：{err_before}")
                return 1
            hit_before, total, _ = field_hit_rate(record, text_before)
            rate_before = hit_before / max(1, total)

            result = preprocess(path, options)
            corrected_path = tmp_dir / (path.stem + "_pp.png")
            cv2.imwrite(str(corrected_path), result.image)
            text_after, err_after = ocr_text(corrected_path)
            if err_after:
                print(f"[FAIL] 校正图 OCR 失败：{err_after}")
                return 1
            hit_after, total_after, _ = field_hit_rate(record, text_after)
            rate_after = hit_after / max(1, total_after)

            before_rates.append(rate_before)
            after_rates.append(rate_after)
            delta = rate_after - rate_before
            print(f"{name:<44}{rate_before:>9.0%}{rate_after:>10.0%}{delta:>+8.0%}")

    # 只统计"原图本身可读"的样本（原图命中率 ≥50%）。
    # 原图就已经读不出来的样本（例如远景小票，字高只有 8–10px），OCR 结果在噪声水平，
    # 拿它做前后对比没有意义——如实单列，不混进平均。
    readable = [(b, a) for b, a in zip(before_rates, after_rates) if b >= 0.5]
    excluded = len(before_rates) - len(readable)
    if readable:
        mean_before = float(np.mean([b for b, _ in readable]))
        mean_after = float(np.mean([a for _, a in readable]))
    else:
        mean_before = mean_after = 0.0
    delta = mean_after - mean_before
    improved = sum(1 for b, a in readable if a > b)
    worsened = sum(1 for b, a in readable if a < b)

    print("-" * 74)
    print(f"可比样本（原图命中率 ≥50%）：{len(readable)} 张；不可比（原图就读不出）：{excluded} 张")
    print(f"平均字段命中率：原图 {mean_before:.0%} → 预处理后 {mean_after:.0%}（{delta:+.0%}）")
    print(f"单张：变好 {improved} 张 / 变差 {worsened} 张 / 持平 {len(readable) - improved - worsened} 张")

    if delta < -0.02:
        print("\n[FAIL] 预处理让 OCR 明显变差（>2 个百分点）——大概率是照度过校正或重采样把字糊了。")
        print("       排查顺序：① 关掉 illum 再跑一次；② 看 max_side 是否过小；③ 检查是否被二次缩放。")
        return 1
    print("\n[OK] 预处理未损伤文字（平均命中率未下降），可用于 S1-3。")
    return 0


if __name__ == "__main__":
    sys.exit(main())