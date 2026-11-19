"""pytest 公共装置。

关键点：`app.main` 在 import 时就会 `create_app()` → 读配置。所以**必须**在导入 app 之前
把环境变量准备好，否则测试会因为缺 DATABASE_URL 直接崩在收集阶段。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 合成样本生成器在仓库根 scripts/ 下（AGENTS §3：脚本一律放根 scripts/），
# 测试直接 import 它们，避免把测试复制一份到 scripts 里。
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# ---- 必须在 import app.* 之前设置 ----
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("DATABASE_URL", "postgresql://studyocr:test@127.0.0.1:5433/studyocr")
os.environ.setdefault("REDIS_URL", "redis://:test@127.0.0.1:6380/0")
os.environ.setdefault("MINIO_ENDPOINT", "127.0.0.1:9002")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("MINIO_BUCKET", "study-ocr-test")
os.environ.setdefault("JWT_SECRET", "test_secret_" + "x" * 32)
# 测试不启动 APScheduler：否则每个 TestClient 都会连 Redis 并注册后台任务
os.environ.setdefault("SCHEDULER_ENABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture()
def settings() -> Settings:
    """测试用配置：不读 backend/.env，避免开发者本地配置影响测试结果。"""
    get_settings.cache_clear()
    return Settings(
        environment="test",
        database_url="postgresql://studyocr:test@127.0.0.1:5433/studyocr",
        redis_url="redis://:test@127.0.0.1:6380/0",
        minio_endpoint="127.0.0.1:9002",
        minio_access_key="test",
        minio_secret_key="test",
        minio_bucket="study-ocr-test",
        jwt_secret="test_secret_" + "x" * 32,
        scheduler_enabled=False,
    )


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    """基础客户端：健康检查用假检查器，避免单测依赖真实服务。"""
    app = create_app(settings=settings)

    @app.get("/api/v1/_test/boom-app-error")
    def _boom_app_error():  # pragma: no cover - 仅测试用
        from app.core.exceptions import NotFoundError

        raise NotFoundError("测试用的业务异常")

    @app.get("/api/v1/_test/boom-crash")
    def _boom_crash():  # pragma: no cover - 仅测试用
        raise RuntimeError("测试用的未捕获异常：不该出现在响应里")

    @app.get("/api/v1/_test/validation/{item_id}")
    def _validation(item_id: int):  # pragma: no cover - 仅测试用
        return {"item_id": item_id}

    return TestClient(app, raise_server_exceptions=False)