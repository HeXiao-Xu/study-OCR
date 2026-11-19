"""文档对账（S2-12）：实际 OpenAPI 路由必须都能在 docs/API.md 里找到。

只检查"已实现 → 有文档"这一个方向；API.md 里标注 S3/S4/S5 的未实现接口不参与。
路径参数名统一归一（`{id}` / `{invoice_id}` → `{}`），避免因为命名差异误报。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.main import create_app

API_DOC = Path(__file__).resolve().parents[2] / "docs" / "API.md"
_DOC_ENDPOINT = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE)\s+`?(/(?:api/v1|health)[A-Za-z0-9_{}/\-]*)"
)


def _normalize(path: str) -> str:
    normalized = re.sub(r"\{[^}]+\}", "{}", path)
    return normalized.rstrip("/") or "/"


def test_every_implemented_route_is_documented() -> None:
    openapi_paths = create_app().openapi()["paths"]
    implemented = {
        (method.upper(), _normalize(path))
        for path, operations in openapi_paths.items()
        for method in operations
    }
    assert implemented, "OpenAPI 没有任何路由，说明应用没挂载"

    documented = {
        (match.group(1), _normalize(match.group(2)))
        for match in _DOC_ENDPOINT.finditer(API_DOC.read_text(encoding="utf-8"))
    }

    missing = sorted(implemented - documented)
    assert not missing, f"API.md 缺少已实现路由（method, path）：{missing}"