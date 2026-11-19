"""模板与规则的出入参（API.md §3 / §11 TemplateOut）。

三类校验在 Pydantic 层完成，路由不再重复：
- `type_code` 格式、`match_keywords` 数量与去空；
- `field_specs_json` 的结构、正则可编译且 ≤500 字符、strategy 白名单；
- `rules[].pattern` 可编译（正则来自后台，属于不受信输入）。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import FieldValidator, TicketType
from app.schemas.common import PageParams

STRATEGIES = ("inline_right", "below_center", "table_column", "label_neighbor", "total_zone")
_DIRECTIONS = ("right", "below", "any")
_VALIDATORS = tuple(member.value for member in FieldValidator)


def _check_regex(value: str, label: str) -> str:
    if len(value) > 500:
        raise ValueError(f"{label} 正则长度不能超过 500")
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"{label} 正则非法：{exc}") from exc
    return value


def _walk_patterns(node: Any, label: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("pattern", "regex") and isinstance(value, str):
                _check_regex(value, label)
            else:
                _walk_patterns(value, label)
    elif isinstance(node, list):
        for item in node:
            _walk_patterns(item, label)


def validate_field_specs(specs: dict[str, Any]) -> None:
    if not isinstance(specs, dict) or not specs:
        raise ValueError("field_specs_json 不能为空")
    for field_name, spec in specs.items():
        if not isinstance(spec, dict):
            raise ValueError(f"{field_name} 的规格必须是对象")
        keywords = spec.get("keywords") or spec.get("anchors")
        if not keywords or not isinstance(keywords, list) or not all(
            isinstance(item, str) and item.strip() for item in keywords
        ):
            raise ValueError(f"{field_name} 必须给出非空的 keywords/anchors")
        strategy = spec.get("strategy")
        if strategy is not None and strategy not in STRATEGIES:
            raise ValueError(f"{field_name} 的 strategy 非法：{strategy}")
        direction = spec.get("direction")
        if direction is not None and direction not in _DIRECTIONS:
            raise ValueError(f"{field_name} 的 direction 非法：{direction}")
        zone = spec.get("zone")
        if zone is not None:
            if (
                not isinstance(zone, list)
                or len(zone) != 2
                or not all(isinstance(x, (int, float)) for x in zone)
                or not 0 <= float(zone[0]) < float(zone[1]) <= 1
            ):
                raise ValueError(f"{field_name} 的 zone 必须是 [y1<y2] 且落在 0–1")
        _walk_patterns(spec, field_name)


class TemplateListQuery(PageParams):
    active: bool = True
    type_code: str | None = Field(default=None, max_length=32)


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type_code: str
    type_name: str
    match_keywords: list[str] | None
    version: int
    active: bool
    created_at: datetime
    updated_at: datetime


class TemplateDetail(TemplateOut):
    field_specs_json: dict[str, Any]


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    field_name: str
    pattern: str | None
    validator: str | None
    error_msg: str | None
    priority: int
    active: bool


class TemplateDetailData(BaseModel):
    template: TemplateDetail
    rules: list[RuleOut]


class RuleIn(BaseModel):
    field_name: str = Field(min_length=1, max_length=32)
    pattern: str | None = None
    validator: Literal[
        "amount", "date", "tax_no", "code_no", "uppercase_amount"
    ] | None = None
    error_msg: str | None = Field(default=None, max_length=200)
    priority: int = Field(default=100, ge=0)
    active: bool = True

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _check_regex(value, "pattern")


class TemplateCreate(BaseModel):
    type_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,31}$")
    type_name: str = Field(min_length=1, max_length=50)
    match_keywords: list[str] = Field(min_length=1, max_length=10)
    field_specs_json: dict[str, Any]
    active: bool = False
    # API.md §12.1：规则随模板 PUT/创建提交，不提供独立 rules CRUD
    rules: list[RuleIn] = Field(default_factory=list)

    @field_validator("type_name")
    @classmethod
    def _trim_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("type_code")
    @classmethod
    def _check_type_code(cls, value: str) -> str:
        # SCHEMA §9.6 的 CHECK 只允许固定票种；不在这里拦，会变成 DB 约束错误（409 而不是 422）
        allowed = {member.value for member in TicketType}
        if value not in allowed:
            raise ValueError(f"type_code 必须是已有票种：{' / '.join(sorted(allowed))}")
        return value

    @field_validator("match_keywords")
    @classmethod
    def _check_keywords(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("match_keywords 不能含空字符串")
            if text not in cleaned:
                cleaned.append(text)
        if not 1 <= len(cleaned) <= 10:
            raise ValueError("match_keywords 必须 1–10 个")
        return cleaned

    @field_validator("field_specs_json")
    @classmethod
    def _check_specs(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_field_specs(value)
        return value

    @field_validator("rules")
    @classmethod
    def _check_rules_unique(cls, value: list[RuleIn]) -> list[RuleIn]:
        names = [item.field_name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("rules 里不能有重复的 field_name")
        return value


class TemplateUpdate(TemplateCreate):
    version: int = Field(ge=1)


class TestFieldOut(BaseModel):
    field_name: str
    value: str
    confidence: float
    bbox: list[int] | None
    source: str


class TemplateTestData(BaseModel):
    template_id: int
    type_code: str
    matched_keywords: list[str]
    overall_confidence: float
    elapsed_ms: int
    fields: list[TestFieldOut]