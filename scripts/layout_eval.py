#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""票种识别准确率评估（S1-4 验收）。

跑真实链路：预处理 → OCR → 票种判定，与标注里的 `ticket_type` 比对。
- 通过线：准确率 ≥ 0.95（任务卡要求）；
- 同时报告 unknown 比例与混淆矩阵——**unknown 不算"判错"，但也不能拿它凑准确率**，
  所以两个数分开看：判对率 = 正确/总数，unknown 率 = 判不出/总数。

用法：
    python scripts/layout_eval.py                 # 全部 60 张
    python scripts/layout_eval.py --limit 20      # 抽样（调试用）
    python scripts/layout_eval.py --split test     # 只看 test 划分
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ocr import get_engine, preprocess  # noqa: E402
from app.ocr.layout import analyze_layout  # noqa: E402

DEFAULT_ANN = ROOT / "backend" / "eval" / "datasets" / "annotations_synth_v1.jsonl"
PASS_THRESHOLD = 0.95


def main() -> int:
    parser = argparse.ArgumentParser(description="票种识别准确率（S1-4）")
    parser.add_argument("--ann", type=Path, default=DEFAULT_ANN)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", choices=["all", "dev", "test"], default="all")
    parser.add_argument("--no-preprocess", action="store_true", help="跳过预处理（对照用）")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    records = [json.loads(line) for line in args.ann.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.split != "all":
        records = [record for record in records if record["split"] == args.split]
    if args.limit:
        records = records[: args.limit]

    engine = get_engine()
    confusion: Counter = Counter()
    rows: list[dict] = []
    correct = unknown = 0

    for index, record in enumerate(records, start=1):
        path = ROOT / record["image"]
        if args.no_preprocess:
            image = cv2.imread(str(path))
        else:
            image = preprocess(path).image
        result = engine.recognize(image)
        layout = analyze_layout(result.lines, (image.shape[1], image.shape[0]))
        predicted = layout.ticket_type
        truth = record["ticket_type"]
        confusion[(truth, predicted)] += 1
        if predicted == truth:
            correct += 1
        elif predicted == "unknown":
            unknown += 1
        rows.append({"image": Path(record["image"]).name, "truth": truth,
                     "predicted": predicted, "score": layout.classification.score,
                     "warnings": layout.classification.warnings,
                     "region_counts": layout.regions.region_counts,
                     "blocks": len(layout.regions.blocks)})
        if index % 10 == 0:
            print(f"  …已处理 {index}/{len(records)}")

    total = len(records)
    accuracy = correct / total if total else 0.0
    print("\n=== 票种识别 ===")
    print(f"样本数：{total} ｜ 判对：{correct} ｜ unknown：{unknown} ｜ "
          f"判错（判成了别的票种）：{total - correct - unknown}")
    print(f"判对率：{accuracy:.1%}（通过线 {PASS_THRESHOLD:.0%}）")

    print("\n混淆矩阵（行=标注，列=预测）：")
    labels = sorted({truth for truth, _ in confusion} | {pred for _, pred in confusion})
    header = "".join(f"{label[:12]:>14s}" for label in labels)
    print(f"{'':>16s}{header}")
    for truth in labels:
        line = "".join(f"{confusion.get((truth, pred), 0):>14d}" for pred in labels)
        print(f"{truth:>16s}{line}")

    misclassified = [row for row in rows if row["predicted"] not in (row["truth"], "unknown")]
    if misclassified:
        print("\n判错的样本：")
        for row in misclassified[:10]:
            print(f"  {row['image']:<44s} 标注={row['truth']:<18s} 预测={row['predicted']}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(
            {"total": total, "correct": correct, "unknown": unknown, "accuracy": round(accuracy, 4),
             "confusion": {f"{k[0]}->{k[1]}": v for k, v in confusion.items()}, "rows": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果：{args.out}")

    if accuracy < PASS_THRESHOLD:
        print(f"\n[FAIL] 判对率 {accuracy:.1%} 低于 {PASS_THRESHOLD:.0%}：先看上面判错的样本，"
              f"多半是关键字/互斥词权重需要调，而不是模板写错")
        return 1
    print("\n[OK] 票种识别达到验收线")
    return 0


if __name__ == "__main__":
    sys.exit(main())