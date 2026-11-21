"""业务日/业务时区换算的唯一入口（AGENTS §4.3：需要业务日时只在一个地方换算）。

数据库存 UTC，接口的"日期区间"按 `Asia/Shanghai` 的自然日解释：
`[date_from 00:00, date_to+1 00:00)` 的半开区间，转成 UTC 后直接用于 `TIMESTAMPTZ` 比较。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.exceptions import ValidationFailedError

__all__ = ["BUSINESS_TZ", "business_day_bounds"]

BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


def business_day_bounds(
    date_from: date | None, date_to: date | None
) -> tuple[datetime | None, datetime | None]:
    """把业务日闭区间 `[from, to]` 换成 UTC 半开区间 `[start, end)`。

    - 任一为空则该端不限制；
    - `from > to` → `422/42200`（参数校验失败，而不是空结果）。
    """
    start = (
        datetime.combine(date_from, time.min, tzinfo=BUSINESS_TZ).astimezone(timezone.utc)
        if date_from is not None
        else None
    )
    end = (
        datetime.combine(
            date_to + timedelta(days=1), time.min, tzinfo=BUSINESS_TZ
        ).astimezone(timezone.utc)
        if date_to is not None
        else None
    )
    if start is not None and end is not None and start >= end:
        raise ValidationFailedError("date_from 不能晚于 date_to")
    return start, end