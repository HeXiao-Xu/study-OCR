"""统一出入参模型。

`Envelope` 是所有接口的响应外壳：`{code, message, data, trace_id}`（API.md §0）。
成功时 `code=0`；失败时 data 为 null，错误结构由 `core/exceptions.py` 统一组装。
"""
from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageParams(BaseModel):
    """列表接口统一入参（API.md §0）。

    FastAPI 0.115+ 可直接 `Annotated[PageParams, Query()]` 当查询参数模型用。
    越界（page=0、page_size=101）由 Pydantic 拦成 422，业务代码不用再判。
    """

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=100, description="每页条数，1–100")
    sort_by: str | None = Field(default=None, description="排序字段（各接口白名单）")
    order: Literal["asc", "desc"] = Field(default="desc", description="asc / desc")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PageResult(BaseModel, Generic[T]):
    """列表接口统一出参：`items / total / page / page_size`。"""

    items: list[T]
    total: int
    page: int
    page_size: int


class Envelope(BaseModel, Generic[T]):
    code: int = Field(default=0, description="0 表示成功；其余见 API.md 错误码表")
    message: str = Field(default="ok")
    data: T | None = None
    trace_id: str = Field(default="-", description="链路追踪 id，与响应头 X-Trace-Id 一致")


class HealthCheckItem(BaseModel):
    ok: bool
    detail: str


class HealthData(BaseModel):
    status: str = Field(description="ok / degraded")
    version: str
    ts: str = Field(description="服务端时间，ISO-8601 UTC")
    checks: dict[str, HealthCheckItem]


def ok_payload(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """在非路由场景（中间件、任务）组装成功响应。"""
    from app.core.exceptions import ok as _ok

    return _ok(data, message)