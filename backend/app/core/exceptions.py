"""统一异常与全局处理器。

对外只允许一种错误结构：`{code, message, data: null, trace_id}`（API.md §0 / §10）。
业务代码抛 `AppError` 子类，禁止在路由里手拼 JSONResponse 或裸 HTTPException（AGENTS.md §10.2）。

两条纪律：
1. **未捕获异常不泄露堆栈**：响应只有通用文案 + trace_id，堆栈进日志；
2. **越权对外一律 404**（API.md §0）：不区分"无权限"与"不存在"，避免探测资源是否存在。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 异常基类与业务子类（错误码见 API.md §10）
# --------------------------------------------------------------------------
class AppError(Exception):
    """所有业务异常的基类。"""

    code: int = 50000
    http_status: int = 500
    message: str = "服务器内部错误"

    def __init__(self, message: str | None = None, *, detail: dict | None = None) -> None:
        self.message = message or self.message
        self.detail = detail or {}
        super().__init__(self.message)


class AuthError(AppError):
    code = 40101
    http_status = 401
    message = "未登录或令牌已失效"


class PermissionDeniedError(AppError):
    """内部判定为无权限；对外由处理器转成 404，记录审计时用原始语义。"""

    code = 40301
    http_status = 403
    message = "无权访问该资源"


class NotFoundError(AppError):
    code = 40400
    http_status = 404
    message = "资源不存在"


class ConflictError(AppError):
    code = 40900
    http_status = 409
    message = "资源冲突"


class DuplicateInvoiceError(AppError):
    code = 40901
    http_status = 409
    message = "该票据已存在"


class PayloadTooLargeError(AppError):
    code = 41300
    http_status = 413
    message = "文件超过大小上限"


class ValidationFailedError(AppError):
    code = 42200
    http_status = 422
    message = "参数校验失败"


class ServiceUnavailableError(AppError):
    code = 50300
    http_status = 503
    message = "依赖服务不可用"


# --------------------------------------------------------------------------
# 响应组装
# --------------------------------------------------------------------------
def current_trace_id(request: Request | None = None) -> str:
    """取当前请求的 trace_id。

    优先用 `request.state.trace_id`（中间件写入）：未捕获异常由最外层
    ServerErrorMiddleware 兜底时，contextvar 已被 reset，只能靠它。
    退化到 contextvar，供非请求场景（任务、脚本）使用。
    """
    from app.core.logging import get_trace_id

    if request is not None:
        trace_id = getattr(request.state, "trace_id", None)
        if trace_id:
            return trace_id
    return get_trace_id()


def envelope(code: int, message: str, data: object = None, trace_id: str | None = None) -> dict:
    """统一响应壳。trace_id 与响应头、日志里的是同一个。"""
    return {"code": code, "message": message, "data": data, "trace_id": trace_id or current_trace_id()}


def ok(data: object = None, message: str = "ok") -> dict:
    return envelope(0, message, data)


# 标准 HTTP 状态 → 业务码（未列出的按 5 位规则兜底）
_HTTP_TO_CODE = {
    400: 40000,
    401: 40101,
    403: 40301,
    404: 40400,
    405: 40500,
    409: 40900,
    413: 41300,
    422: 42200,
    429: 42900,
    500: 50000,
    503: 50300,
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # 越权对外统一 404（不泄露资源存在性），但日志保留真实语义
        http_status = 404 if isinstance(exc, PermissionDeniedError) else exc.http_status
        code = NotFoundError.code if isinstance(exc, PermissionDeniedError) else exc.code
        trace_id = current_trace_id(request)
        log = logger.warning if http_status < 500 else logger.error
        log(
            "业务异常",
            extra={
                "event": "app_error",
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": http_status,
                "error_code": code,
            },
        )
        return JSONResponse(status_code=http_status, content=envelope(code, exc.message, trace_id=trace_id))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 只回摘要，不回完整输入（避免把请求体里的敏感值回显到日志/响应）
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first.get("loc", ())[1:]) or "(root)"
        reason = first.get("msg", "参数不合法")
        trace_id = current_trace_id(request)
        logger.warning(
            "参数校验失败",
            extra={
                "event": "validation_error",
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": 422,
                "error_code": ValidationFailedError.code,
            },
        )
        return JSONResponse(
            status_code=422,
            content=envelope(ValidationFailedError.code, f"参数校验失败：{field} {reason}", trace_id=trace_id),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_TO_CODE.get(exc.status_code, exc.status_code * 100)
        message = {404: "资源不存在", 405: "方法不允许"}.get(exc.status_code, str(exc.detail))
        trace_id = current_trace_id(request)
        logger.warning(
            "HTTP 异常",
            extra={
                "event": "http_error",
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "error_code": code,
            },
        )
        return JSONResponse(status_code=exc.status_code, content=envelope(code, message, trace_id=trace_id))

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 堆栈只进日志；响应只给通用文案 + trace_id，用户凭 trace_id 找开发定位
        trace_id = current_trace_id(request)
        logger.exception(
            "未捕获异常",
            extra={
                "event": "unhandled_exception",
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": 500,
                "error_code": AppError.code,
            },
        )
        return JSONResponse(
            status_code=500, content=envelope(AppError.code, AppError.message, trace_id=trace_id)
        )