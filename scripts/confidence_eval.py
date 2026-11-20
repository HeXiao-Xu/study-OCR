"""S3-2 置信度验收：N 张合成票据离线跑（预处理→识别→抽取→置信度）。

用法（CPU，40 张约 1.5–2 分钟）：
    cd /e/BiSheXZY/study-OCR
    .venv/Scripts/python.exe scripts/confidence_eval.py --samples 40

不变量（任一不成立 → 退出码 1）：
1. 阈值扫描：字段需人工数 / 整单需核对数随阈值**单调不减**；
2. `validators` 判错的字段 `score <= 0.45`（任务卡"直接 <0.5"）；
3. `level=auto` 的字段 `score >= 0.90`；`level=review` 的字段要么 <0.90、要么是冲突字段；
4. 整单 `needs_review` 与字段 `level` 一一对应。

结果写 `backend/eval/results/confidence_eval_<ts>.json`（不入库）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)          # Settings 从 backend/.env 读，和其他脚本一致

from app.core.config import get_settings  # noqa: E402
from app.ocr.detector import OcrOptions, get_engine  # noqa: E402
from app.ocr.extractor import extract_fields, load_field_rules  # noqa: E402
from app.ocr.intake import prepare_pages  # noqa: E402
from app.ocr.layout import analyze_layout, load_templates  # noqa: E402
from app.ocr.validators import run_validators  # noqa: E402
from app.services.confidence import (  # noqa: E402
    FieldScore,
    aggregate_invoice,
    resolve_field,
)

SAMPLE_DIR = ROOT / "samples" / "synth_v1"
RESULT_DIR = BACKEND / "eval" / "results"
THRESHOLDS = (0.50, 0.60, 0.70, 0.80, 0.90, 0.95)


def _collect(samples, engine, rules, templates) -> list[dict]:
    records: list[dict] = []
    for path in samples:
        prepared = prepare_pages(path, max_pages=1)
        ocr = engine.recognize(prepared[0].image)
        layout = analyze_layout(ocr.lines, ocr.image_size, templates)
        extracted = extract_fields(ocr.lines, ocr.image_size, layout.ticket_type, layout, rules)
        failed = run_validators(extracted.fields, layout.ticket_type).failed_fields()

        resolved, flags = [], []
        for name, field in extracted.fields.items():
            has_conflict = len(field.candidates) > 1
            resolved.append(
                resolve_field(
                    FieldScore(
                        field_name=name,
                        score=float(field.confidence),
                        conflict=has_conflict,
                        validator_failed=name in failed,
                    )
                )
            )
            flags.append(
                {"name": name, "conflict": has_conflict, "validator_failed": name in failed}
            )

        invoice_conf = aggregate_invoice(resolved)
        records.append(
            {
                "file": path.name,
                "ticket_type": layout.ticket_type,
                "missing_required": extracted.missing_required,
                "fields": [
                    {"name": item.field_name, "score": item.score, "level": item.level}
                    for item in resolved
                ],
                "flags": flags,
                "invoice": {
                    "overall": invoice_conf.overall,
                    "needs_review": invoice_conf.needs_review,
                    "counts": invoice_conf.counts,
                },
            }
        )
    return records


def _sweep(records: list[dict], threshold: float) -> dict[str, int]:
    return {
        "fields": sum(
            1 for rec in records for field in rec["fields"] if field["score"] < threshold
        ),
        "invoices": sum(
            1
            for rec in records
            if any(field["score"] < threshold for field in rec["fields"])
            or bool(rec["missing_required"])
        ),
    }


def _problems(records: list[dict], sweep: dict[str, dict[str, int]],
              auto_confirm: float, validator_cap: float) -> list[str]:
    problems: list[str] = []
    for key in ("fields", "invoices"):
        series = [entry[key] for entry in sweep.values()]
        if series != sorted(series):
            problems.append(f"阈值扫描不单调：{key} = {series}")

    for rec in records:
        for field, flag in zip(rec["fields"], rec["flags"]):
            if flag["validator_failed"] and field["score"] > validator_cap + 1e-9:
                problems.append(
                    f"{rec['file']}/{field['name']} 校验失败但 score={field['score']}"
                )
            if field["level"] == "auto" and field["score"] < auto_confirm - 1e-9:
                problems.append(
                    f"{rec['file']}/{field['name']} auto 但 score={field['score']}"
                )
            if (
                field["level"] == "review"
                and field["score"] >= auto_confirm - 1e-9
                and not flag["conflict"]
            ):
                problems.append(
                    f"{rec['file']}/{field['name']} review 但 score={field['score']} 且无冲突"
                )
        expected = sorted(
            field["name"] for field in rec["fields"] if field["level"] != "auto"
        )
        if rec["invoice"]["needs_review"] != expected:
            problems.append(f"{rec['file']} needs_review 与字段 level 不一致")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="S3-2 置信度验收")
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    samples = sorted(SAMPLE_DIR.glob("*.jpg"))[: args.samples]
    if len(samples) < args.samples:
        print(f"样例不足：需要 {args.samples} 张，实际 {len(samples)} 张", file=sys.stderr)
        return 2

    settings = get_settings()
    engine = get_engine(OcrOptions.from_settings(settings))
    records = _collect(samples, engine, load_field_rules(), load_templates())
    sweep = {f"{threshold:.2f}": _sweep(records, threshold) for threshold in THRESHOLDS}
    problems = _problems(
        records,
        sweep,
        auto_confirm=settings.confidence_auto_confirm,
        validator_cap=settings.confidence_validator_cap,
    )

    levels = Counter(field["level"] for rec in records for field in rec["fields"])
    scores = [field["score"] for rec in records for field in rec["fields"]]
    buckets = Counter(f"{(score // 0.1) / 10:.1f}" for score in scores)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(records),
        "fields": len(scores),
        "thresholds": sweep,
        "levels": dict(levels),
        "score_buckets": dict(sorted(buckets.items())),
        "problems": problems,
        "records": records,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = args.out or RESULT_DIR / f"confidence_eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"样本 {len(records)} 张 / 字段 {len(scores)} 个；三档分布 {dict(levels)}")
    print("阈值   字段需人工  整单需核对")
    for threshold, entry in sweep.items():
        print(f"{threshold}  {entry['fields']:>8}  {entry['invoices']:>10}")
    print("分数分布：" + "  ".join(f"{key}~{buckets[key]}" for key in sorted(buckets)))
    if problems:
        print(f"[FAIL] 不变量失败 {len(problems)} 条：")
        for item in problems[:10]:
            print(f"  - {item}")
    else:
        print("[OK] 不变量全过（单调性 + 校验封顶 + auto/review 口径）")
    print(f"报告：{out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())