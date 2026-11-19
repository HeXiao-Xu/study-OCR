"""模板注册表：active 模板的 60s 缓存（API.md §3"改规则 60s 内热生效"）。

S3-1 的流水线会从这里拿模板做分类/抽取；S2-8 的读路径（`/templates/{id}/test` 之外的运行时读取）
也用它。写操作必须调用 `registry.invalidate()`，否则最长 60s 才能看到新版本。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ticket_templates import TicketTemplate


@dataclass(frozen=True)
class TemplateSnapshot:
    id: int
    type_code: str
    type_name: str
    match_keywords: list[str] = field(default_factory=list)
    field_specs_json: dict = field(default_factory=dict)
    version: int = 1


class TemplateRegistry:
    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self._ttl = ttl_seconds
        self._loaded_at = 0.0
        self._cache: dict[str, TemplateSnapshot] = {}

    def active_templates(self, db: Session) -> dict[str, TemplateSnapshot]:
        now = time.monotonic()
        if self._cache and (now - self._loaded_at) < self._ttl:
            return self._cache

        rows = db.scalars(
            select(TicketTemplate).where(TicketTemplate.active.is_(True))
        ).all()
        self._cache = {
            row.type_code: TemplateSnapshot(
                id=row.id,
                type_code=row.type_code,
                type_name=row.type_name,
                match_keywords=list(row.match_keywords or []),
                field_specs_json=dict(row.field_specs_json or {}),
                version=row.version,
            )
            for row in rows
        }
        self._loaded_at = now
        return self._cache

    def get(self, db: Session, type_code: str) -> TemplateSnapshot | None:
        return self.active_templates(db).get(type_code)

    def invalidate(self) -> None:
        self._cache = {}
        self._loaded_at = 0.0


registry = TemplateRegistry()