#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标注校验器（S1-1）。

标注错了，后面所有评测都是错的——而且错得很隐蔽。这个脚本就是防线：
格式、坐标、字段可追溯性、业务等式、校验位、数据划分，六类问题一次查完。

用法：
    python scripts/check_annotations.py
    python scripts/check_annotations.py --file backend/eval/datasets/annotations_synth_v1.jsonl --strict
退出码：0 通过；1 有问题（逐条打印）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "backend" / "eval" / "datasets" / "annotations_synth_v1.jsonl"

REQUIRED_KEYS = {"image", "ticket_type", "form_factor", "width", "height", "split", "annotator", "seed", "perturbations", "boxes", "fields"}
TICKET_TYPES = {"vat_general", "train_ticket", "flight_itinerary", "taxi_ticket"}
FORM_FACTORS = {"landscape", "portrait_strip", "portrait_screenshot", "receipt"}
CANONICAL_FIELDS = {
    "invoice_code", "invoice_no", "invoice_date", "buyer_name", "buyer_tax_no",
    "seller_name", "seller_tax_no", "amount", "tax_amount", "total_amount",
    "check_code", "passenger_name",
}
# 版式标签（不是字段，但要允许出现在 boxes 里）
LAYOUT_LABELS = {"title", "label", "status_bar", "table_header", "table_row", "qrcode", "footer",
                 "seal_zone", "crease", "paper_zone", "station_from", "station_to", "station_arrow",
                 "depart_time", "seat", "id_card_masked", "train_no", "flight_no", "cabin", "plate",
                 "total_amount_upper"}

_MONEY_RE = re.compile(r"^[¥￥]?-?\d{1,3}(,\d{3})*(\.\d{2})?$")
_USCC_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def uscc_ok(code: str) -> bool:
    if len(code) != 18 or any(ch not in _USCC_CHARSET for ch in code):
        return False
    total = sum(_USCC_CHARSET.index(ch) * w for ch, w in zip(code[:17], _USCC_WEIGHTS))
    remainder = 31 - total % 31
    return _USCC_CHARSET[0 if remainder == 31 else remainder] == code[17]


class Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, where: str, message: str) -> None:
        self.items.append(f"{where}: {message}")

    def __bool__(self) -> bool:
        return bool(self.items)


def check_record(record: dict, index: int, problems: Problems) -> str | None:
    where = f"第 {index + 1} 行"
    missing = REQUIRED_KEYS - record.keys()
    if missing:
        problems.add(where, f"缺字段 {sorted(missing)}")
        return None

    image_rel = record["image"]
    image_path = ROOT / image_rel
    where = f"第 {index + 1} 行（{Path(image_rel).name}）"
    if not image_path.exists():
        problems.add(where, f"图片不存在：{image_rel}")
        return None

    if record["ticket_type"] not in TICKET_TYPES:
        problems.add(where, f"非法 ticket_type：{record['ticket_type']}")
    if record["form_factor"] not in FORM_FACTORS:
        problems.add(where, f"非法 form_factor：{record['form_factor']}")
    if record["split"] not in {"dev", "test"}:
        problems.add(where, f"非法 split：{record['split']}")

    width, height = int(record["width"]), int(record["height"])
    boxes = record["boxes"]
    if not boxes:
        problems.add(where, "boxes 为空")
        return hashlib.sha256(image_path.read_bytes()).hexdigest()

    for box_index, box in enumerate(boxes):
        label = box.get("label")
        if not (label in CANONICAL_FIELDS or label in LAYOUT_LABELS):
            problems.add(where, f"boxes[{box_index}] 非法 label：{label}")
        bbox = box.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, int) for v in bbox)):
            problems.add(where, f"boxes[{box_index}].bbox 必须是 4 个整数：{bbox}")
            continue
        x1, y1, x2, y2 = bbox
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            problems.add(where, f"boxes[{box_index}].bbox 越界：{bbox}（图 {width}x{height}）")

    fields = record["fields"]
    if not fields:
        problems.add(where, "fields 为空")
    for name, meta in fields.items():
        if name not in CANONICAL_FIELDS:
            problems.add(where, f"fields 出现非规范字段：{name}")
            continue
        box_index = meta.get("box_index")
        if not isinstance(box_index, int) or not (0 <= box_index < len(boxes)):
            problems.add(where, f"fields[{name}].box_index 非法：{box_index}")
            continue
        if boxes[box_index].get("text") != meta.get("text"):
            problems.add(where, f"fields[{name}].text 与 boxes[{box_index}].text 不一致")
        if boxes[box_index].get("label") != name:
            problems.add(where, f"fields[{name}] 指向的框 label 是 {boxes[box_index].get('label')}")
        value = str(meta.get("value", ""))
        if name in {"amount", "tax_amount", "total_amount"}:
            try:
                Decimal(value)
            except InvalidOperation:
                problems.add(where, f"fields[{name}].value 不是数字：{value}")
            if not _MONEY_RE.match(str(meta.get("text", ""))):
                problems.add(where, f"fields[{name}].text 不是合法金额格式：{meta.get('text')}")
        if name in {"buyer_tax_no", "seller_tax_no"} and not uscc_ok(value):
            problems.add(where, f"fields[{name}] 统一社会信用代码校验位不通过：{value}")
        if name == "invoice_date" and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            problems.add(where, f"fields[invoice_date] 归一值应为 YYYY-MM-DD：{value}")
        if name == "invoice_no" and not re.match(r"^\d{8}$", value):
            problems.add(where, f"fields[invoice_no] 应为 8 位数字：{value}")
        if name == "invoice_code" and not re.match(r"^(\d{10}|\d{12})$", value):
            problems.add(where, f"fields[invoice_code] 应为 10/12 位数字：{value}")
        if name == "check_code" and not re.match(r"^\d{20}$", value):
            problems.add(where, f"fields[check_code] 应为 20 位数字：{value}")

    # 金额三件套等式（合成数据的硬约束）
    if {"amount", "tax_amount", "total_amount"} <= fields.keys():
        try:
            amount = Decimal(str(fields["amount"]["value"]))
            tax = Decimal(str(fields["tax_amount"]["value"]))
            total = Decimal(str(fields["total_amount"]["value"]))
            if abs(total - (amount + tax)) > Decimal("0.01"):
                problems.add(where, f"金额等式不成立：{amount} + {tax} != {total}")
        except InvalidOperation:
            pass

    return hashlib.sha256(image_path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="标注校验（S1-1）")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--strict", action="store_true", help="额外检查：每个票种在 dev/test 都有样本")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if not args.file.exists():
        print(f"[FAIL] 标注文件不存在：{args.file}")
        print("       先运行：python scripts/make_samples.py")
        return 1

    problems = Problems()
    hashes: dict[str, str] = {}
    records = 0
    split_count = {"dev": 0, "test": 0}
    type_in_split: dict[str, set[str]] = {"dev": set(), "test": set()}

    for line_no, raw in enumerate(args.file.read_text(encoding="utf-8").splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.add(f"第 {line_no} 行", f"JSON 解析失败：{exc}")
            continue
        records += 1
        digest = check_record(record, line_no - 1, problems)
        if digest:
            if digest in hashes:
                problems.add(f"第 {line_no} 行", f"与 {hashes[digest]} 图像完全相同（重复样本）")
            hashes[digest] = Path(record["image"]).name
            split = record.get("split", "")
            if split in split_count:
                split_count[split] += 1
                type_in_split[split].add(record.get("ticket_type", ""))

    if args.strict:
        for ticket_type in TICKET_TYPES:
            for split in ("dev", "test"):
                if ticket_type not in type_in_split[split]:
                    problems.add("划分", f"{split} 里没有 {ticket_type} 的样本")

    print(f"标注文件：{args.file.relative_to(ROOT)}")
    print(f"样本数：{records} ｜ dev={split_count['dev']} ｜ test={split_count['test']}")
    print(f"票种覆盖：dev={sorted(type_in_split['dev'])} ｜ test={sorted(type_in_split['test'])}")

    if problems:
        print(f"\n[FAIL] 发现 {len(problems.items)} 个问题：")
        for item in problems.items[:40]:
            print("  -", item)
        if len(problems.items) > 40:
            print(f"  … 另有 {len(problems.items) - 40} 条")
        return 1
    print("\n[OK] 全部校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())