"""列表分页与排序协议（API.md §0）。

两条纪律：
1. **count 与数据必须是同一个 `Select`**：`paginate` 内部只在同一个 stmt 上去掉 order_by 取子查询，
   不存在"列表用一套 where、total 用另一套"的经典 bug；
2. **排序只接受白名单 key**：用户永远传不到真实列名，`order` 由 Pydantic 的 Literal 限定为 asc/desc。

端点用法（S2-5 起）：
    stmt = filter_visible(select(Invoice), Invoice, user).where(...)   # 业务筛选
    page = paginate(db, stmt, Invoice, SORT_MAP, params, default="created_at")
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.exceptions import ValidationFailedError
from app.schemas.common import PageParams, PageResult

SortMap = Mapping[str, InstrumentedAttribute[Any]]


def apply_sort(
    stmt: Select[Any],
    model: type[Any],
    sort_map: SortMap,
    params: PageParams,
    *,
    default: str | None = None,
    nulls_last: bool = False,
) -> Select[Any]:
    """按白名单追加 ORDER BY；未知字段抛 42200，不把用户输入拼进 SQL。

    `nulls_last`：可空排序列（如 `report_forms.submitted_at`）要显式把 NULL 放最后。
    PG 的 `DESC` 默认 `NULLS FIRST`，草稿会盖在已提交单据上面——默认 False 保持旧行为不变。
    """
    key = params.sort_by or default
    if key is None:
        # 接口必须显式声明默认排序：分页层不猜业务
        raise ValidationFailedError("接口未声明默认排序字段")
    column = sort_map.get(key)
    if column is None:
        allowed = " / ".join(sort_map)
        raise ValidationFailedError(f"sort_by 只能是：{allowed}")

    primary = column.desc() if params.order == "desc" else column.asc()
    if nulls_last:
        primary = primary.nulls_last()
    # 稳定排序：排序键相同（同秒 created_at、同金额）时按 id 升序，翻页不重复也不漏行
    return stmt.order_by(primary, model.id.asc())


def paginate(
    db: Session,
    stmt: Select[Any],
    model: type[Any],
    sort_map: SortMap,
    params: PageParams,
    *,
    default: str | None = None,
    scalar: bool = True,
    nulls_last: bool = False,
) -> PageResult:
    """排序 → 同一 stmt 求 total → offset/limit 取页。

    `scalar=True`（默认）用于 `select(Invoice)` 这类单实体查询，items 是 ORM 对象；
    列表需要带出关联列时用 `select(Invoice).join(...).add_columns(...)` + `scalar=False`，
    items 是 SQLAlchemy Row，一次查询拿全，不做逐行回查。
    """
    ordered = apply_sort(
        stmt, model, sort_map, params, default=default, nulls_last=nulls_last
    )

    total = db.scalar(
        select(func.count()).select_from(ordered.order_by(None).subquery())
    )
    page_stmt = ordered.offset(params.offset).limit(params.page_size)
    if scalar:
        rows = db.scalars(page_stmt).all()
    else:
        rows = db.execute(page_stmt).all()

    return PageResult(
        items=list(rows),
        total=int(total or 0),
        page=params.page,
        page_size=params.page_size,
    )