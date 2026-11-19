"""结构化日志 + trace_id 贯穿。

约束（AGENTS.md §8 / 任务卡坑位）：
- 只此一套日志设施，不引入 loguru / structlog（两套日志等于没有日志）；
- **每条日志都带 trace_id**，否则排查问题时会退化成 grep 时间戳；
- 日志里禁止出现口令、私钥、会话密钥——脱敏的最终防线在 `app/utils/masking.py`（后续任务落地）。
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings

# 请求级上下文：中间件负责 set/reset，日志格式化器负责读取
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

# 允许通过 extra={...} 带进日志的附加字段白名单（防止误把敏感数据塞进日志）
_EXTRA_FIELDS = (
    "event",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "client_ip",
    "task_id",
    "invoice_id",
    "report_id",
    "stage",
    "error_code",
    "checks",
    # 认证相关（S2-2）：只允许哈希后的用户名与用户 id，禁止明文口令
    "username_sha256",
    "user_id",
    "status",
    "reason",
)


def get_trace_id() -> str:
    return trace_id_var.get()


def set_trace_id(trace_id: str):
    """设置当前上下文的 trace_id，返回 token 供 reset 使用。"""
    return trace_id_var.set(trace_id)


def reset_trace_id(token) -> None:
    trace_id_var.reset(token)


class JsonFormatter(logging.Formatter):
    """把 LogRecord 渲染成单行 JSON，便于后续接日志采集（也便于人眼 grep trace_id）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None) or get_trace_id(),
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            # 异常堆栈进日志、不进接口响应（接口侧由 exceptions.py 保证）
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(settings: Settings) -> None:
    """配置根 logger。可重复调用（reload / 多次 create_app 不会叠加 handler）。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # uvicorn 自带 logger 也挂到根上，避免同一行日志出现两次
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True