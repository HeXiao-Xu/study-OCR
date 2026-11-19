#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字段级准确率评估（S1-5 验收）。

对 60 张样本跑完整识别链路，把抽出来的字段与标注逐字段比对，打一张**字段级准确率表**：
- 每个字段：`标注里存在的样本数` / `抽对` / `抽错` / `漏抽` / `多抽(标注里没有却抽了)`；
- 准确率 = 抽对 / 标注里存在该字段的样本数；
- 金额按 Decimal 比（0.01 容差），日期按 ISO 字符串，其余按字符串归一化后精确比。

`test` 划分只用于报告，不参与调参；这里默认跑全部，避免样本太少看不出问题。

用法：
    python scripts/field_eval.py
    python scripts/field_eval.py --split test --out backend/eval/results/field_eval_test.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ocr import get_engine, preprocess  # noqa: E402
from app.ocr.extractor import extract_fields  # noqa: E402
from app.ocr.layout import analyze_layout  # noqa: E402
from app.ocr.validators import parse_money  # noqa: E402

DEFAULT_ANN = ROOT / "backend" / "eval" / "datasets" / "annotations_synth_v1.jsonl"


def _normalize_expected(name: str, value: str) -> str:
    text = str(value).strip()
    if name in {"amount", "tax_amount", "total_amount"}:
        amount = parse_money(text)
        return f"{amount:.2f}" if amount is not None else text
    if name == "invoice_date":
        return text  # 标注已是 ISO
    return re.sub(r"\s", "", text).upper()


def _equal(name: str, expected: str, actual: str) -> bool:
    if name in {"amount", "tax_amount", "total_amount"}:
        left, right = parse_money(expected), parse_money(actual)
        return left is not None and right is not None and abs(left - right) <= Decimal("0.01")
    return _normalize_expected(name, expected) == _normalize_expected(name, actual)


def main() -> int:
    parser = argparse.ArgumentParser(description="字段级准确率（S1-5）")
    parser.add_argument("--ann", type=Path, default=DEFAULT_ANN)
    parser.add_argument("--split", choices=["all", "dev", "test"], default="all")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-preprocess", action="store_true")
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
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"expected": 0, "correct": 0, "wrong": 0, "missed": 0})
    extras: dict[str, int] = defaultdict(int)
    rows: list[dict] = []

    for index, record in enumerate(records, start=1):
        path = ROOT / record["image"]
        image = preprocess(path).image if not args.no_preprocess else None
        if image is None:
            import cv2

            image = cv2.imread(str(path))
        ocr = engine.recognize(image)
        layout = analyze_layout(ocr.lines, (image.shape[1], image.shape[0]))
        extracted = extract_fields(ocr.lines, (image.shape[1], image.shape[0]), layout.ticket_type, layout)

        for name, spec in record["fields"].items():
            expected = _normalize_expected(name, spec["value"])
            bucket = stats[name]
            bucket["expected"] += 1
            field = extracted.fields.get(name)
            if field is None:
                bucket["missed"] += 1
            elif _equal(name, expected, field.value):
                bucket["correct"] += 1
            else:
                bucket["wrong"] += 1

        for name in extracted.fields:
            if name not in record["fields"] and name not in {"total_amount_upper"}:
                extras[name] += 1

        rows.append({
            "image": Path(record["image"]).name,
            "ticket_type": layout.ticket_type,
            "truth_type": record["ticket_type"],
            "fields": {name: field.value for name, field in extracted.fields.items()},
            "missing_required": extracted.missing_required,
            "conflicts": [item["field"] for item in extracted.conflicts],
            "warnings": extracted.warnings,
        })
        if index % 10 == 0:
            print(f"  …已处理 {index}/{len(records)}")

    print("\n=== 字段级准确率 ===")
    print(f"{'字段':<20}{'应抽':>6}{'抽对':>6}{'抽错':>6}{'漏抽':>6}{'准确率':>8}")
    print("-" * 54)
    total_expected = total_correct = 0
    for name in sorted(stats):
        bucket = stats[name]
        total_expected += bucket["expected"]
        total_correct += bucket["correct"]
        rate = bucket["correct"] / bucket["expected"] if bucket["expected"] else 0.0
        print(f"{name:<20}{bucket['expected']:>6}{bucket['correct']:>6}"
              f"{bucket['wrong']:>6}{bucket['missed']:>6}{rate:>8.1%}")
    overall = total_correct / total_expected if total_expected else 0.0
    print("-" * 54)
    print(f"{'合计':<20}{total_expected:>6}{total_correct:>6}"
          f"{'':>6}{'':>6}{overall:>8.1%}")
    if extras:
        print(f"\n标注里没有、但被抽出来的字段（可能是误抽）：{dict(extras)}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "split": args.split, "samples": len(records), "overall": round(overall, 4),
            "per_field": {name: bucket for name, bucket in stats.items()},
            "extras": dict(extras), "rows": rows,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n结果：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())