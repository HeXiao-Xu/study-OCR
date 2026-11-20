"""抽取/校验规则的唯一来源（S3-6）：DB active 模板优先，库缺该票种才回退 YAML。

为什么单独一个模块：流水线（抽取）与整单确认（必填集/校验）必须用**同一套**规则；
两份实现迟早漂移，而"确认用的必填集和识别用的规则不一样"是最难查的一类不一致。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ocr.extractor import FieldRule, load_field_rules, rules_from_specs
from app.services import template_registry

__all__ = ["ResolvedRules", "resolve_rules"]


@dataclass(frozen=True)
class ResolvedRules:
    rules: list[FieldRule]
    source: str                  # "db" | "yaml"
    template_id: int | None


def resolve_rules(db: Session, ticket_type: str, *, registry=None) -> ResolvedRules:
    reg = registry or template_registry.registry
    snapshot = reg.active_templates(db).get(ticket_type)
    if snapshot is not None and snapshot.field_specs_json:
        return ResolvedRules(
            rules=rules_from_specs(snapshot.field_specs_json),
            source="db",
            template_id=snapshot.id,
        )
    return ResolvedRules(rules=load_field_rules(), source="yaml", template_id=None)