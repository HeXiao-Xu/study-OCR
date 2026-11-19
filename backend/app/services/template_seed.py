"""S1 YAML → 数据库模板的 seed 构建（纯函数，便于单测）。

只做"把出厂默认搬成 DB 行"：layout YAML 提供 type_code/type_name/match_keywords；
field_rules.yaml 提供字段锚定与校验规格，按 `tickets` 过滤成每个模板的 `field_specs_json`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "ocr" / "templates"
RULES_PATH = TEMPLATE_DIR / "field_rules.yaml"

_VALIDATOR_BY_FIELD = {
    "amount": "amount",
    "tax_amount": "amount",
    "total_amount": "amount",
    "invoice_date": "date",
    "buyer_tax_no": "tax_no",
    "seller_tax_no": "tax_no",
    "invoice_code": "code_no",
    "invoice_no": "code_no",
}


@dataclass(frozen=True)
class SeedRule:
    field_name: str
    pattern: str | None
    validator: str | None
    error_msg: str | None
    priority: int = 100


@dataclass(frozen=True)
class SeedTemplate:
    type_code: str
    type_name: str
    match_keywords: list[str]
    field_specs_json: dict[str, dict]
    rules: list[SeedRule] = field(default_factory=list)


def _strategy_for(field_name: str, direction: str) -> str:
    if field_name in {"amount", "tax_amount", "total_amount"}:
        return "total_zone"
    if field_name in {"buyer_name", "seller_name", "passenger_name"}:
        return "label_neighbor"
    if direction == "below":
        return "below_center"
    if direction == "right":
        return "inline_right"
    return "label_neighbor"


def _spec_from_rule(name: str, spec: dict) -> dict:
    direction = spec.get("direction", "right")
    return {
        "strategy": _strategy_for(name, direction),
        "keywords": list(spec.get("anchors") or []),
        "direction": direction,
        "regex": spec.get("regex"),
        "max_distance": spec.get("max_distance"),
        "zone": spec.get("zone"),
        "occurrence": spec.get("occurrence", "all"),
        "required": bool(spec.get("required", False)),
        "if_multiple": spec.get("if_multiple"),
        "value_index": int(spec.get("value_index", 0)),
        "value_type": spec.get("value_type", "text"),
        "exclude": list(spec.get("exclude") or []) or None,
        "whole_line_anchors": list(spec.get("whole_line_anchors") or []) or None,
        "tickets": list(spec.get("tickets") or []) or None,
    }


def build_seed_templates(
    template_dir: Path | None = None, rules_path: Path | None = None
) -> list[SeedTemplate]:
    directory = template_dir or TEMPLATE_DIR
    rules_path = rules_path or RULES_PATH

    layouts: list[dict] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "type_code" in data:  # field_rules.yaml 没有 type_code
            layouts.append(data)
    if not layouts:
        raise FileNotFoundError(f"没有找到票种模板：{directory}")

    field_rules: dict = (
        yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    ).get("fields") or {}
    if not field_rules:
        raise ValueError(f"字段规则为空：{rules_path}")

    seeds: list[SeedTemplate] = []
    for layout in layouts:
        type_code = str(layout["type_code"])
        keywords = [
            str(item["text"])
            for item in (layout.get("keywords") or [])
            if isinstance(item, dict) and item.get("text")
        ][:10]
        if not keywords:
            raise ValueError(f"{type_code} 没有 match keywords")

        specs: dict[str, dict] = {}
        rules: list[SeedRule] = []
        for name, spec in field_rules.items():
            tickets = spec.get("tickets")
            if tickets and type_code not in tickets:
                continue
            specs[name] = _spec_from_rule(name, spec)
            validator = _VALIDATOR_BY_FIELD.get(name)
            rules.append(
                SeedRule(
                    field_name=name,
                    pattern=spec.get("regex"),
                    validator=validator,
                    error_msg=f"{name} 格式不正确" if validator else None,
                )
            )

        seeds.append(
            SeedTemplate(
                type_code=type_code,
                type_name=str(layout.get("type_name") or type_code),
                match_keywords=keywords,
                field_specs_json=specs,
                rules=rules,
            )
        )
    return seeds