#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成票据集生成器（S1-1）。

产出：
- 图像：`samples/synth_v1/*.jpg`（本地生成，**不入库**）
- 标注：`backend/eval/datasets/annotations_synth_v1.jsonl`（入库，可复现）

流程固定为：**定字段值 → 排版（坐标由绘制过程记录）→ 几何扰动（图像与框同步变换）
→ 光度扰动 → 叠加印章/折痕 → 落盘 + 写标注**。

固定种子即可逐字节复现同一批数据（Pillow 版本一致时）。

用法：
    python scripts/make_samples.py                     # 默认 40 张、种子 20261118
    python scripts/make_samples.py --n 80 --seed 7     # 扩量
    python scripts/make_samples.py --clean-ratio 0.1   # 干净样本比例
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import perturb
from invoice_templates import render

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "samples" / "synth_v1"
DEFAULT_ANN = ROOT / "backend" / "eval" / "datasets" / "annotations_synth_v1.jsonl"

# 12 个规范字段（与 SCHEMA.md / 方案 6.3 一致）
CANONICAL_FIELDS = {
    "invoice_code", "invoice_no", "invoice_date", "buyer_name", "buyer_tax_no",
    "seller_name", "seller_tax_no", "amount", "tax_amount", "total_amount",
    "check_code", "passenger_name",
}

# 版式清单：(ticket_type, form_factor, 张数, dev 张数)
LAYOUTS: list[tuple[str, str, int, int]] = [
    ("vat_general", "landscape", 10, 6),
    ("vat_general", "portrait_screenshot", 8, 5),
    ("train_ticket", "portrait_strip", 8, 5),
    ("flight_itinerary", "landscape", 7, 4),
    ("taxi_ticket", "receipt", 7, 4),
]


@dataclass
class Sample:
    path: Path
    rel_path: str
    ticket_type: str
    form_factor: str
    width: int
    height: int
    split: str
    seed: int
    perturbations: list[str]
    boxes: list[dict]
    fields: dict[str, dict]


def _apply_perturbations(image, drawn, rng: random.Random, *, clean: bool, allow_background: bool):
    """按顺序施加扰动，返回 (图像, 框, 生效的扰动名列表)。

    顺序有讲究：**先贴桌面 → 再几何 → 再光度 → 最后盖章**。
    贴背景必须在几何之前，否则旋转的是"已经裁好的纸面"，等于没考验票据定位。
    """
    applied: list[str] = []
    if clean:
        return image, drawn, applied

    # ---- 背景：把纸面放到桌面上（截图形态除外）----
    # padding 三档混合：近景（纸面 70%+，最常见的报销拍照）、中景、远景（纸面 20–35%，
    # 对齐本次真实样本那种"离得很远、纸面只占三成"的困难工况）。
    if allow_background and rng.random() < 0.85:
        roll = rng.random()
        if roll < 0.55:
            pad = (0.06, 0.20)
        elif roll < 0.85:
            pad = (0.25, 0.45)
        else:
            pad = (0.50, 0.85)
        image, drawn = perturb.paste_on_background(image, drawn, rng, pad_ratio=pad)
        applied.append("background")

    # ---- 几何（图像与框同步变换）----
    if rng.random() < 0.85:
        image, drawn = perturb.rotate(image, drawn, rng.uniform(-6, 6))
        applied.append("rotate")
    if rng.random() < 0.7:
        image, drawn = perturb.perspective(image, drawn, rng.uniform(0.005, 0.04), rng)
        applied.append("perspective")

    # ---- 光度（不改几何）----
    if rng.random() < 0.9:
        image = perturb.illumination(image, rng)
        applied.append("illumination")
    if rng.random() < 0.8:
        image = perturb.color_cast(image, rng)
        applied.append("color_cast")
    if rng.random() < 0.6:
        image = perturb.noise(image, rng.uniform(2, 8), rng)
        applied.append("noise")
    if rng.random() < 0.35:
        image = perturb.blur(image, rng.choice([0.8, 1.0, 1.5]))
        applied.append("blur")

    # ---- 叠加物（最后画，才是"盖在字上"）----
    text_area = sum((b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]) for b in drawn if b.label != "crease")
    if rng.random() < 0.75:
        image, seal = perturb.overprint_seal(image, rng, text_area=text_area)
        drawn = [*drawn, seal]
        applied.append("seal")
    if rng.random() < 0.3:
        image, crease = perturb.crease(image, rng)
        drawn = [*drawn, crease]
        applied.append("crease")

    if not applied:  # 至少一项，否则等于干净样本
        image, drawn = perturb.rotate(image, drawn, rng.uniform(-3, 3))
        applied.append("rotate")
    return image, drawn, applied


def build_samples(out_dir: Path, ann_path: Path, seed: int, clean_ratio: float) -> list[Sample]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ann_path.parent.mkdir(parents=True, exist_ok=True)

    samples: list[Sample] = []
    seq = 0
    for ticket_type, form_factor, total, dev_count in LAYOUTS:
        for i in range(total):
            seq += 1
            # 每张票的种子由主种子 + 序号派生，保证"改一张不影响其它张"
            ticket_seed = seed * 1000 + seq
            rng = random.Random(ticket_seed)
            spec_rng = random.Random(ticket_seed + 1)
            fx_rng = random.Random(ticket_seed + 2)

            rendered = render(ticket_type, form_factor, spec_rng)
            image = rendered.canvas.image
            drawn = list(rendered.canvas.drawn)

            clean = rng.random() < clean_ratio
            # 手机截图形态不贴桌面（它是"屏幕上的发票"，不是"桌上的纸"）
            allow_bg = rendered.form_factor != "portrait_screenshot"
            image, drawn, applied = _apply_perturbations(
                image, drawn, fx_rng, clean=clean, allow_background=allow_bg
            )

            name = f"{ticket_type}_{form_factor}_{i + 1:03d}.jpg"
            path = out_dir / name
            image.save(path, format="JPEG", quality=88)

            boxes = [item.as_dict(line_no=index) for index, item in enumerate(drawn)]
            # 字段仍指向原来的下标（扰动不改变顺序），但 text 以最终 boxes 为准
            fields: dict[str, dict] = {}
            for field_name, meta in rendered.fields.items():
                if field_name not in CANONICAL_FIELDS:
                    raise SystemExit(f"[bug] 模板产出了非规范字段：{field_name}（{ticket_type}）")
                index = meta["box_index"]
                fields[field_name] = {
                    "text": boxes[index]["text"],
                    "value": meta["value"],
                    "box_index": index,
                    "bbox": boxes[index]["bbox"],
                }

            samples.append(Sample(
                path=path,
                rel_path=path.relative_to(ROOT).as_posix(),
                ticket_type=ticket_type,
                form_factor=form_factor,
                width=image.width,
                height=image.height,
                split="dev" if i < dev_count else "test",
                seed=ticket_seed,
                perturbations=applied,
                boxes=boxes,
                fields=fields,
            ))

    with ann_path.open("w", encoding="utf-8", newline="\n") as handle:
        for sample in samples:
            record = {
                "image": sample.rel_path,
                "ticket_type": sample.ticket_type,
                "form_factor": sample.form_factor,
                "width": sample.width,
                "height": sample.height,
                "split": sample.split,
                "annotator": "synth",
                "seed": sample.seed,
                "perturbations": sample.perturbations,
                "boxes": sample.boxes,
                "fields": sample.fields,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return samples


def report(samples: list[Sample]) -> None:
    """打印分布与成像统计（不做断言，断言在 verify_samples.py）。"""
    print(f"生成 {len(samples)} 张")
    by_layout: dict[str, int] = {}
    for s in samples:
        by_layout[f"{s.ticket_type}/{s.form_factor}"] = by_layout.get(f"{s.ticket_type}/{s.form_factor}", 0) + 1
    for key, count in by_layout.items():
        print(f"  {key:45s} {count}")
    dev = sum(1 for s in samples if s.split == "dev")
    print(f"  dev={dev}  test={len(samples) - dev}")

    means, stds, reds, paper_ratios = [], [], [], []
    for s in samples:
        arr = np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(s.path).convert("RGB"), dtype=float)
        gray = arr.mean(axis=2)
        means.append(gray.mean())
        stds.append(gray.std())
        red = ((arr[:, :, 0] > 120) & (arr[:, :, 0] - arr[:, :, 1] > 40) & (arr[:, :, 0] - arr[:, :, 2] > 40))
        reds.append(100 * red.mean())
        paper = next((b for b in s.boxes if b["label"] == "paper_zone"), None)
        if paper:
            x1, y1, x2, y2 = paper["bbox"]
            paper_ratios.append(100.0 * (x2 - x1) * (y2 - y1) / (s.width * s.height))
    print("成像统计（对照真实样本：均值 129 / σ 64 / 红 33.5% / 纸面占比 ~30%）")
    print(f"  灰度均值 {np.mean(means):.1f}   σ均值 {np.mean(stds):.1f}   红占比均值 {np.mean(reds):.1f}%")
    if paper_ratios:
        print(f"  纸面占比均值 {np.mean(paper_ratios):.1f}%（有背景的样本 {len(paper_ratios)} 张）")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成合成票据集与标注（S1-1）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ann", type=Path, default=DEFAULT_ANN)
    parser.add_argument("--seed", type=int, default=20261118)
    parser.add_argument("--clean-ratio", type=float, default=0.12,
                        help="干净样本比例（默认 12%%：可读性检查需要足够的无扰动对照）")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    samples = build_samples(args.out, args.ann, args.seed, args.clean_ratio)
    report(samples)
    print(f"\n标注：{args.ann.relative_to(ROOT)}")
    print(f"图像：{args.out.relative_to(ROOT)}（不入库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())