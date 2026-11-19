#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端基线跑批（S1-6）。

一条命令产出论文第一章要用的数字：检测 Hmean / 字符准确率 / 字段级准确率 / 整单正确率 /
待确认率与低置信命中率 / 耗时分解，并输出可直接贴进论文的 Markdown 表。

指标实现全在 `backend/eval/metrics.py`（纯函数 + 单测），本脚本只负责"跑数据 → 调指标"。
这样"打分"与"产结果"是两套代码，不会自证。

用法：
    python scripts/ocr_baseline.py                        # 用 baseline 配置跑全量
    python scripts/ocr_baseline.py --split test           # 只跑 test 划分
    python scripts/ocr_baseline.py --compare-preprocess   # 顺带跑一次"预处理关掉"的对照
    python scripts/ocr_baseline.py --dir samples/synth_v1 # 无标注目录：只统计行数与耗时
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import cv2
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend"))  # 允许 `from eval.metrics import ...`

from app.ocr import OcrOptions, get_engine, preprocess  # noqa: E402
from app.ocr.extractor import extract_fields, load_field_rules  # noqa: E402
from app.ocr.layout import analyze_layout  # noqa: E402
from app.ocr.preprocess import PreprocessOptions  # noqa: E402
from eval.metrics import (  # noqa: E402
    char_accuracy, document_accuracy, edit_distance, field_accuracy, field_correct,
    coverage_match, need_review_metrics, percentile,
)

DEFAULT_CONFIG = ROOT / "backend" / "eval" / "configs" / "baseline.yaml"
RESULTS_DIR = ROOT / "backend" / "eval" / "results"
# 这些标注框不是"文字行"，不能作为检测/字符准确率的真值
NON_TEXT_LABELS = {"paper_zone", "seal_zone", "qrcode", "crease"}


def _load_config(path_or_name: str) -> dict:
    candidate = Path(path_or_name)
    if not candidate.is_absolute():
        candidate = (ROOT / "backend" / "eval" / "configs" / f"{path_or_name}.yaml") \
            if not candidate.suffix else (ROOT / candidate)
    if not candidate.exists():
        raise FileNotFoundError(f"配置不存在：{candidate}")
    return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}


def _preprocess_options(config: dict, *, enabled: bool | None = None) -> PreprocessOptions | None:
    section = config.get("preprocess") or {}
    use = section.get("enabled", True) if enabled is None else enabled
    if not use:
        return None
    return PreprocessOptions(
        rectify=section.get("rectify", True),
        illum=section.get("illum", True),
        denoise=section.get("denoise", True),
        detect_seal=section.get("detect_seal", True),
        max_side=int(section.get("max_side", 1600)),
    )


def _ocr_options(config: dict) -> OcrOptions:
    section = config.get("ocr") or {}
    return OcrOptions(
        use_angle_cls=section.get("use_angle_cls", True),
        det_db_unclip_ratio=float(section.get("det_db_unclip_ratio", 1.6)),
        det_limit_side_len=int(section.get("det_limit_side_len", 1600)),
        drop_score=float(section.get("drop_score", 0.0)),
    )


def _gt_lines(record: dict) -> list[dict]:
    """把**元素级**标注框合并成**行级**真值。

    标注是按"画上去的每个文本元素"给的（一行的 4 个单元格是 4 个框），
    而 OCR 引擎输出的是**整行**。不合并的话 IoU 匹配会把"4 个单元格 vs 1 行"
    算成 1 TP + 3 FN，检测指标毫无意义（实测 Hmean 只有 0.26）。
    合并规则：y 方向重叠超过较矮者一半的行 → 同一行；x 取并集，文本按 x 顺序拼接。
    """
    boxes = [dict(box) for box in record["boxes"]
             if box.get("text") and box.get("label") not in NON_TEXT_LABELS]
    boxes.sort(key=lambda box: (box["bbox"][1], box["bbox"][0]))
    lines: list[dict] = []
    for box in boxes:
        x1, y1, x2, y2 = box["bbox"]
        height = max(1, y2 - y1)
        placed = False
        for line in lines:
            ly1, ly2 = line["bbox"][1], line["bbox"][3]
            overlap = min(y2, ly2) - max(y1, ly1)
            if overlap > 0.5 * min(height, max(1, ly2 - ly1)):
                line["bbox"] = [min(line["bbox"][0], x1), min(line["bbox"][1], y1),
                                max(line["bbox"][2], x2), max(line["bbox"][3], y2)]
                line["parts"].append((x1, box["text"]))
                placed = True
                break
        if not placed:
            lines.append({"bbox": [x1, y1, x2, y2], "parts": [(x1, box["text"])]})
    for line in lines:
        line["parts"].sort(key=lambda part: part[0])
        line["text"] = "".join(text for _, text in line["parts"])
    lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
    return lines


def _transform_boxes(boxes: list[tuple], matrix) -> list[tuple]:
    """把标注框从**原图坐标**映到**校正图坐标**。

    这是很容易踩的坑：标注是按原图给的，而 OCR 跑在透视校正后的图上。
    不换坐标系，IoU 恒等于 0（实测 Hmean 只有 0.09），指标完全没有意义。
    """
    if matrix is None:
        return boxes
    import numpy as np

    transformed = []
    for x1, y1, x2, y2 in boxes:
        corners = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2)
        transformed.append((float(projected[:, 0].min()), float(projected[:, 1].min()),
                            float(projected[:, 0].max()), float(projected[:, 1].max())))
    return transformed


def _gt_text_boxes(record: dict) -> list[tuple]:
    """检测真值用**元素级**框（不合并成行）。

    `coverage_match` 就是为"元素级标注 vs 行级输出"设计的：一行覆盖 4 个单元格 →
    4 个标注都算命中，而这一行本身只算一个有效预测。合并成行反而会让框形对不上。
    """
    return [tuple(box["bbox"]) for box in record["boxes"]
            if box.get("text") and box.get("label") not in NON_TEXT_LABELS]


def _gt_text(record: dict) -> str:
    return "".join(line["text"] for line in _gt_lines(record))


def evaluate(records: list[dict], config: dict, *, use_preprocess: bool | None = None,
             engine=None) -> dict:
    """跑一批样本并算全部指标。返回可直接 JSON 化的 dict。"""
    engine = engine or get_engine(_ocr_options(config))
    pp_options = _preprocess_options(config, enabled=use_preprocess)
    metrics_config = config.get("metrics") or {}
    iou_threshold = float(metrics_config.get("iou_threshold", 0.5))
    review_threshold = float(metrics_config.get("need_review_threshold", 0.7))
    rules = load_field_rules(Path(ROOT / config["rules"])) if config.get("rules") else None

    detags = {"tp": 0, "fp": 0, "fn": 0}
    char_rates: list[float] = []
    field_rows: list[dict] = []
    reviewed_rows: list[dict] = []
    doc_rows: list[dict] = []
    timings = defaultdict(list)
    per_type: dict[str, dict] = defaultdict(lambda: {"doc": [], "field": [], "count": 0})
    details: list[dict] = []

    for record in records:
        path = ROOT / record["image"]
        started = time.perf_counter()
        if pp_options is not None:
            pp = preprocess(path, pp_options)
            image = pp.image
            timings["preprocess"].append(pp.timings_ms["total"])
        else:
            pp = None
            image = cv2.imread(str(path))
            timings["preprocess"].append(0.0)

        ocr = engine.recognize(image)
        timings["ocr"].append(ocr.elapsed_ms)

        layout_started = time.perf_counter()
        layout = analyze_layout(ocr.lines, (image.shape[1], image.shape[0]))
        timings["layout"].append((time.perf_counter() - layout_started) * 1000)

        extract_started = time.perf_counter()
        extracted = extract_fields(ocr.lines, (image.shape[1], image.shape[0]),
                                   layout.ticket_type, layout, rules)
        timings["extract"].append((time.perf_counter() - extract_started) * 1000)
        timings["total"].append((time.perf_counter() - started) * 1000)

        # ---- 检测 ----
        # 标注框在原图坐标；预处理后 OCR 输出在校正图坐标 → 先把标注映射过去
        if pp is not None:
            import numpy as np

            original_to_corrected = np.linalg.inv(pp.to_original)
            gt_boxes = _transform_boxes(_gt_text_boxes(record), original_to_corrected)
        else:
            gt_boxes = _gt_text_boxes(record)
        pred_boxes = [(line.box, line.score) for line in ocr.lines]
        score = coverage_match(pred_boxes, gt_boxes, iou_threshold)
        detags["tp"] += score.tp
        detags["fp"] += score.fp
        detags["fn"] += score.fn

        # ---- 字符准确率 ----
        predicted_text = "".join(line.text for line in ocr.lines)
        gt_text = _gt_text(record)
        char_rates.append(char_accuracy(predicted_text, gt_text))

        # ---- 字段 ----
        required: list[bool] = []
        for name, spec in record["fields"].items():
            expected = spec["value"]
            field = extracted.fields.get(name)
            correct = field_correct(name, expected, field.value if field else None)
            field_rows.append({"field": name, "correct": correct, "ticket_type": record["ticket_type"]})
            per_type[record["ticket_type"]]["field"].append(correct)
            required.append(correct)
            if field is not None:
                reviewed_rows.append({"confidence": field.confidence, "correct": correct})

        doc_ok = bool(required) and all(required)
        doc_rows.append({"image": record["image"], "required_correct": required})
        per_type[record["ticket_type"]]["doc"].append(doc_ok)
        per_type[record["ticket_type"]]["count"] += 1

        details.append({
            "image": Path(record["image"]).name,
            "ticket_type": layout.ticket_type,
            "truth_type": record["ticket_type"],
            "lines": len(ocr.lines),
            "gt_lines": len(gt_boxes),
            "det_tp": score.tp, "det_fp": score.fp, "det_fn": score.fn,
            "char_accuracy": round(char_rates[-1], 4),
            "doc_correct": doc_ok,
            "missing_required": extracted.missing_required,
            "conflicts": [item["field"] for item in extracted.conflicts],
        })

    tp = detags["tp"]
    precision = tp / (tp + detags["fp"]) if (tp + detags["fp"]) else 0.0
    recall = tp / (tp + detags["fn"]) if (tp + detags["fn"]) else 0.0
    hmean = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "samples": len(records),
        "preprocess_enabled": pp_options is not None,
        "detection": {"tp": tp, "fp": detags["fp"], "fn": detags["fn"],
                      "precision": round(precision, 4), "recall": round(recall, 4),
                      "hmean": round(hmean, 4), "iou_threshold": iou_threshold},
        "char_accuracy": round(sum(char_rates) / len(char_rates), 4) if char_rates else 0.0,
        "field_accuracy": field_accuracy(field_rows),
        "field_overall": round(sum(1 for row in field_rows if row["correct"]) / len(field_rows), 4)
        if field_rows else 0.0,
        "document_accuracy": round(document_accuracy(doc_rows), 4),
        "need_review": need_review_metrics(reviewed_rows, review_threshold),
        "timing_ms": {name: {"p50": round(percentile(values, 0.5), 1),
                             "p95": round(percentile(values, 0.95), 1),
                             "mean": round(sum(values) / len(values), 1) if values else 0.0}
                      for name, values in timings.items()},
        "by_ticket_type": {
            name: {
                "samples": bucket["count"],
                "field_accuracy": round(sum(bucket["field"]) / len(bucket["field"]), 4)
                if bucket["field"] else 0.0,
                "document_accuracy": round(sum(bucket["doc"]) / len(bucket["doc"]), 4)
                if bucket["doc"] else 0.0,
            }
            for name, bucket in per_type.items()
        },
        "details": details,
    }


def _markdown(report: dict, config_name: str) -> str:
    lines: list[str] = []
    lines.append(f"# 基线评测报告（{config_name}）\n")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 样本数：{report['runs'][0]['samples']}（数据集：`{report['dataset']}`，划分：{report['split']}）")
    lines.append(f"- 环境：Python {report['environment']['python']}，CPU 推理，paddleocr {report['environment']['paddleocr']}\n")

    lines.append("## 1. 总体指标\n")
    lines.append("| 指标 | 数值 | 口径 |")
    lines.append("|---|---|---|")
    for run in report["runs"]:
        tag = "（预处理开）" if run["preprocess_enabled"] else "（预处理关）"
        det = run["detection"]
        lines.append(f"| 检测 Hmean{tag} | **{det['hmean']:.3f}** | 覆盖匹配（IoU≥{det['iou_threshold']} 或 标注 85% 被预测包含） |")
        lines.append(f"| 检测 P / R{tag} | {det['precision']:.3f} / {det['recall']:.3f} | TP={det['tp']} FP={det['fp']} FN={det['fn']} |")
        lines.append(f"| 字符准确率{tag} | **{run['char_accuracy']:.3f}** | 1 − 编辑距离/标注长度 |")
        lines.append(f"| 字段级准确率{tag} | **{run['field_overall']:.3f}** | 逐字段精确匹配 |")
        lines.append(f"| 整单正确率{tag} | **{run['document_accuracy']:.3f}** | 全部标注字段都对 |")
        review = run["need_review"]
        lines.append(f"| 待确认率 / 低置信命中率{tag} | {review['need_review_rate']:.3f} / "
                     f"{review['low_conf_error_rate']:.3f} | 阈值见配置 |")
    lines.append("")

    lines.append("## 2. 字段级准确率\n")
    header = "| 字段 | " + " | ".join(
        ("预处理开" if run["preprocess_enabled"] else "预处理关") for run in report["runs"]) + " |"
    lines.append(header)
    lines.append("|---|" + "---|" * len(report["runs"]))
    field_names = sorted({name for run in report["runs"] for name in run["field_accuracy"]})
    for name in field_names:
        cells = []
        for run in report["runs"]:
            bucket = run["field_accuracy"].get(name)
            cells.append(f"{bucket['accuracy']:.3f}（{bucket['correct']}/{bucket['count']}）" if bucket else "—")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 3. 按票种\n")
    lines.append("| 票种 | " + " | ".join(
        f"{'预处理开' if run['preprocess_enabled'] else '预处理关'} 字段级 / 整单" for run in report["runs"]) + " |")
    lines.append("|---|" + "---|" * len(report["runs"]))
    types = sorted({name for run in report["runs"] for name in run["by_ticket_type"]})
    for name in types:
        cells = []
        for run in report["runs"]:
            bucket = run["by_ticket_type"].get(name)
            cells.append(f"{bucket['field_accuracy']:.3f} / {bucket['document_accuracy']:.3f}"
                         if bucket else "—")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 4. 耗时分解（ms）\n")
    lines.append("| 阶段 | " + " | ".join(
        f"{'开' if run['preprocess_enabled'] else '关'} P50 / P95" for run in report["runs"]) + " |")
    lines.append("|---|" + "---|" * len(report["runs"]))
    for stage in ("preprocess", "ocr", "layout", "extract", "total"):
        cells = []
        for run in report["runs"]:
            timing = run["timing_ms"].get(stage)
            cells.append(f"{timing['p50']:.0f} / {timing['p95']:.0f}" if timing else "—")
        lines.append(f"| {stage} | " + " | ".join(cells) + " |")
    lines.append("\n> 说明：整单正确率必然显著低于字段级准确率（多个字段各自 0.9，整单只有约 0.9^N），"
                 "论文中必须同时报告并解释这一点。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="端到端基线跑批（S1-6）")
    parser.add_argument("--config", default="baseline", help="配置名或路径")
    parser.add_argument("--dir", type=Path, help="无标注目录：只统计行数与耗时")
    parser.add_argument("--split", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--compare-preprocess", action="store_true", help="顺带跑一次预处理关闭的对照")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    config = _load_config(args.config)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_base = args.out or (RESULTS_DIR / f"baseline_{timestamp}")
    out_base.parent.mkdir(parents=True, exist_ok=True)

    engine = get_engine(_ocr_options(config))

    if args.dir:
        images = sorted(args.dir.glob("*.jpg")) + sorted(args.dir.glob("*.png"))
        if args.limit:
            images = images[: args.limit]
        print(f"无标注模式：{len(images)} 张，只统计行数与耗时")
        rows = []
        for path in images:
            t0 = time.perf_counter()
            image = preprocess(path).image
            ocr = engine.recognize(image)
            rows.append({"image": path.name, "lines": len(ocr.lines),
                         "mean_score": round(ocr.mean_score, 4),
                         "ocr_ms": ocr.elapsed_ms,
                         "total_ms": round((time.perf_counter() - t0) * 1000, 1)})
        report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                  "dataset": str(args.dir), "split": "n/a", "runs": [{"samples": len(rows)}],
                  "rows": rows}
        (out_base.with_suffix(".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
        print(f"平均行数 {sum(r['lines'] for r in rows) / max(1, len(rows)):.1f}，"
              f"OCR P50 {percentile([r['ocr_ms'] for r in rows], 0.5):.0f}ms")
        print(f"结果：{out_base.with_suffix('.json')}")
        return 0

    dataset = ROOT / config["dataset"]
    records = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    split = args.split or config.get("split", "all")
    if split != "all":
        records = [record for record in records if record["split"] == split]
    limit = args.limit if args.limit is not None else int(config.get("limit", 0))
    if limit:
        records = records[:limit]

    print(f"配置：{args.config} ｜ 样本 {len(records)} ｜ 划分 {split}")
    runs = []
    print("→ 预处理开 …")
    runs.append(evaluate(records, config, use_preprocess=True, engine=engine))
    if args.compare_preprocess:
        print("→ 预处理关（对照）…")
        runs.append(evaluate(records, config, use_preprocess=False, engine=engine))

    import paddleocr

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": config.get("name", args.config),
        "dataset": config["dataset"],
        "split": split,
        "environment": {"python": sys.version.split()[0],
                        "paddleocr": getattr(paddleocr, "__version__", "unknown"),
                        "platform": os.name},
        "runs": runs,
    }
    (out_base.with_suffix(".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                               encoding="utf-8")
    markdown = _markdown(report, config.get("name", args.config))
    report_path = RESULTS_DIR / "baseline_report.md"
    report_path.write_text(markdown, encoding="utf-8")

    main_run = runs[0]
    print(f"\n检测 Hmean {main_run['detection']['hmean']:.3f} ｜ "
          f"字符准确率 {main_run['char_accuracy']:.3f} ｜ "
          f"字段级 {main_run['field_overall']:.3f} ｜ 整单 {main_run['document_accuracy']:.3f}")
    print(f"结果：{out_base.with_suffix('.json').relative_to(ROOT)}")
    print(f"报告：{report_path.relative_to(ROOT)}")

    if main_run["document_accuracy"] < 0.5:
        print("\n[WARN] 整单正确率 < 0.5：按任务卡要求应先回 S1-2/S1-5 排查，"
              "而不是直接拿这组数字写论文。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())