"""健康检查路由。

按 API.md §9：`GET /health`（无前缀，容器探针用）与 `GET /api/v1/health`（业务前缀，前端用）
是**同一个实现**，响应体一致。做法是这个 router 只声明 `/health`，
在 main.py 里挂两次（无前缀 + `settings.api_prefix`），避免两处维护同一段逻辑。

依赖异常时返回 **503 + status=degraded**，Docker / Nginx 探针能直接判活。

注意：端点用同步 `def`，让 FastAPI 丢到线程池执行——三个检查都是阻塞 IO，
写成 `async def` 会卡住事件循环（FastAPI 最常见的性能事故之一）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.exceptions import envelope
from app.core.health import run_checks
from app.schemas.common import HealthData

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/health", response_model=None, summary="健康检查（依赖 DB / Redis / MinIO）")
def health() -> JSONResponse:
    settings = get_settings()
    try:
        checks = run_checks(settings)
    except Exception:  # noqa: BLE001 - 探针自身故障也要给出结构化的降级结果
        # 细节只进日志；响应里不回异常类型/堆栈（对外统一"不可用"语义）
        logger.exception("健康检查执行失败")
        checks = {"internal": {"ok": False, "detail": "健康检查执行失败"}}
    healthy = all(item["ok"] for item in checks.values())
    payload = HealthData(
        status="ok" if healthy else "degraded",
        version=settings.app_version,
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        checks=checks,
    ).model_dump()
    http_status = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=http_status,
        content=envelope(0, "ok" if healthy else "degraded", payload),
    )