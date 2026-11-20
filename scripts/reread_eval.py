"""S3-3 局部重识别验收：N 张合成票据离线跑，挑低置信字段做裁剪重识别。

用法（CPU，40 张约 2–3 分钟）：
    cd /e/BiSheXZY/study-OCR
    .venv/Scripts/python.exe scripts/reread_eval.py --samples 40

口径：
- 候选字段 = 全链路抽取后 `confidence < 0.90` 且 `source_line_no` 非空；
- 对每个字段按 `ocr/refine.reread_box` 做一次裁剪放大重识别，记录 old/new 文本与分数；
- "提升" = `new_score > old_confidence + 0.05`（0.05 是本项目判定"明显上升"的最小差）；
- 输出可重识别字段数、提升数/率、平均提升、按字段分组、top 5 样例与异常；
- 结果写 `backend/eval/results/reread_eval_<ts>.json`（不入库）。

注意：这里**不改任何库**，只回答"裁剪重识别到底有没有用"；写回只在 worker 里做（S3-3 run_reocr）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)          # Settings 从 backend/.env 读，和其他脚本一致

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import ValidationFailedError  # noqa: E402
from app.ocr import refine  # noqa: E402
from app.ocr.detector import OcrOptions, get_engine  # noqa: E402
from app.ocr.extractor import extract_fields, load_field_rules  # noqa: E402
from app.ocr.intake import prepare_pages  # noqa: E402
from app.ocr.layout import analyze_layout, load_templates  # noqa: E402

SAMPLE_DIR = ROOT / "samples" / "synth_v1"
RESULT_DIR = BACKEND / "eval" / "results"
LOW_CONFIDENCE = 0.90
IMPROVE_MARGIN = 0.05


def _collect(samples, engine, rules, templates, settings) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    errors: list[dict] = []
    for path in samples:
        page = prepare_pages(path, max_pages=1)[0]
        ocr = engine.recognize(page.image)
        layout = analyze_layout(ocr.lines, ocr.image_size, templates)
        extracted = extract_fields(ocr.lines, ocr.image_size, layout.ticket_type, layout, rules)

        for name, field in extracted.fields.items():
            if float(field.confidence) >= LOW_CONFIDENCE or field.source_line_no is None:
                continue
            line = next(
                (item for item in ocr.lines if item.line_no == field.source_line_no), None
            )
            if line is None:
                errors.append({"file": path.name, "field": name, "error": "missing_line"})
                continue
            try:
                candidate = refine.reread_box(
                    page.image,
                    line.box,
                    engine=engine,
                    factor=settings.reread_scale,
                    pad_min_px=settings.reread_pad_min_px,
                    pad_ratio=settings.reread_pad_ratio,
                )
            except ValidationFailedError as exc:
                errors.append({"file": path.name, "field": name, "error": str(exc)})
                continue

            old = float(field.confidence)
            records.append({
                "file": path.name,
                "ticket_type": layout.ticket_type,
                "field": name,
                "old_value": field.value,
                "old_confidence": round(old, 3),
                "new_text": candidate.text,
                "new_score": round(candidate.score, 3),
                "delta": round(candidate.score - old, 3),
                "improved": candidate.score > old + IMPROVE_MARGIN,
                "bbox": list(candidate.bbox),
            })
    return records, errors


def _summarise(records: list[dict]) -> dict:
    improved = [item for item in records if item["improved"]]
    by_field: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "improved": 0})
    for item in records:
        bucket = by_field[item["field"]]
        bucket["count"] += 1
        bucket["improved"] += int(item["improved"])
    return {
        "candidates": len(records),
        "improved": len(improved),
        "improve_rate": round(len(improved) / len(records), 3) if records else 0.0,
        "avg_delta": round(sum(item["delta"] for item in records) / len(records), 3)
        if records else 0.0,
        "by_field": {name: dict(stats) for name, stats in sorted(by_field.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="S3-3 局部重识别验收")
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    samples = sorted(SAMPLE_DIR.glob("*.jpg"))[: args.samples]
    if len(samples) < args.samples:
        print(f"样例不足：需要 {args.samples} 张，实际 {len(samples)} 张", file=sys.stderr)
        return 2

    settings = get_settings()
    engine = get_engine(OcrOptions.from_settings(settings))
    records, errors = _collect(
        samples, engine, load_field_rules(), load_templates(), settings
    )
    summary = _summarise(records)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(samples),
        "low_confidence_threshold": LOW_CONFIDENCE,
        "improve_margin": IMPROVE_MARGIN,
        **summary,
        "errors": errors,
        "records": records,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or RESULT_DIR / f"reread_eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"样本 {len(samples)} 张；低置信可重识别字段 {summary['candidates']} 个")
    print(f"提升 {summary['improved']} 个（{summary['improve_rate']:.1%}），"
          f"平均 Δ {summary['avg_delta']:+.3f}（阈值 +{IMPROVE_MARGIN}）")
    print("按字段：")
    for name, stats in summary["by_field"].items():
        print(f"  {name:<20} {stats['improved']}/{stats['count']} 提升")
    top = sorted((item for item in records if item["improved"]),
                 key=lambda item: item["delta"], reverse=True)[:5]
    if top:
        print("提升最大样例：")
        for item in top:
            print(f"  {item['file']} {item['field']}: {item['old_confidence']:.2f} → "
                  f"{item['new_score']:.2f} | {item['old_value']!r} → {item['new_text']!r}")
    else:
        print("没有任何字段提升超过阈值——如实记录，别把它写成成功")
    if errors:
        print(f"异常 {len(errors)} 条（前 5）：{errors[:5]}")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())