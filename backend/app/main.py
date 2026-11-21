"""应用入口：create_app() 工厂 + trace_id 中间件。

为什么用工厂而不是模块级 `app = FastAPI()`：
- 测试可以传自己的 `Settings`（指向测试库 / 假检查器），不必改环境变量再重载模块；
- 后续 uvicorn 多 worker 启动时，配置只在工厂里读一次，行为可预测。
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.files import router as files_router
from app.api.v1.health import router as health_router
from app.api.v1.invoices import router as invoices_router
from app.api.v1.templates import router as templates_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import reset_trace_id, set_trace_id, setup_logging
from app.tasks.scheduler import shutdown_scheduler, start_scheduler

access_logger = logging.getLogger("app.access")

_MAX_TRACE_ID_LEN = 64


def _resolve_trace_id(request: Request, header_name: str) -> str:
    """优先复用调用方传入的 trace_id（便于跨服务串联），非法则新生成。"""
    incoming = request.headers.get(header_name, "").strip()
    if incoming and len(incoming) <= _MAX_TRACE_ID_LEN and all(
        char.isalnum() or char in "-_." for char in incoming
    ):
        return incoming
    return uuid4().hex


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 调度器随 API 启动；多副本靠任务内的 Redis 锁保证同一次只执行一遍
        start_scheduler(settings)
        try:
            yield
        finally:
            shutdown_scheduler()

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.app_version,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[settings.trace_id_header],
    )

    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next) -> Response:
        trace_id = _resolve_trace_id(request, settings.trace_id_header)
        # 同时挂到 request.state：Starlette 的 ServerErrorMiddleware 在中间件**外层**，
        # 未捕获异常时 contextvar 已经 reset，处理器只能从这里拿 trace_id。
        request.state.trace_id = trace_id
        token = set_trace_id(trace_id)
        started = time.perf_counter()

        try:
            response: Response = await call_next(request)
        except Exception:
            # 兜底：正常情况异常已被 exception_handler 转成响应，走到这里说明处理器本身出错。
            # 日志必须在 reset 之前写，否则格式化器取不到 trace_id。
            access_logger.exception(
                "request failed",
                extra={
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            reset_trace_id(token)
            raise

        # 响应头必带 trace_id：用户报障时贴这一行就能定位整条链路
        response.headers[settings.trace_id_header] = trace_id
        access_logger.info(
            "request",
            extra={
                "event": "http_request",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "client_ip": request.client.host if request.client else None,
            },
        )
        reset_trace_id(token)
        return response

    register_exception_handlers(app)

    # 健康检查挂两次：`/health`（容器探针）+ `{api_prefix}/health`（前端与文档口径一致，API.md §9）
    app.include_router(health_router)
    app.include_router(health_router, prefix=settings.api_prefix)
    app.include_router(auth_router, prefix=settings.api_prefix)
    # upload 先于 invoices：`/invoices/upload` 必须命中上传，而不是被 `/{invoice_id}` 抢走
    app.include_router(files_router, prefix=settings.api_prefix)
    app.include_router(invoices_router, prefix=settings.api_prefix)
    app.include_router(templates_router, prefix=settings.api_prefix)
    app.include_router(admin_router, prefix=settings.api_prefix)

    return app


app = create_app()