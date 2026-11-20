"""配置校验：缺项/非法项必须在启动阶段炸掉，而不是等到第一个请求。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import ConfigError, Settings, get_settings

_ENV_KEYS = (
    "APP_NAME",
    "APP_VERSION",
    "ENVIRONMENT",
    "LOG_LEVEL",
    "API_PREFIX",
    "CORS_ORIGINS",
    "DATABASE_URL",
    "REDIS_URL",
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_BUCKET",
    "MINIO_SECURE",
    "JWT_SECRET",
    "MAX_UPLOAD_MB",
    "HEALTH_TIMEOUT_S",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """本文件的用例只看显式传参：清空所有配置相关环境变量，并切到空目录（读不到 .env）。"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    yield

_BASE = dict(
    database_url="postgresql://u:p@127.0.0.1:5433/db",
    redis_url="redis://:p@127.0.0.1:6380/0",
    minio_endpoint="127.0.0.1:9002",
    minio_access_key="ak",
    minio_secret_key="sk",
    minio_bucket="bucket",
    jwt_secret="s" * 32,
)


def _settings(**overrides) -> Settings:
    data = {**_BASE, **overrides}
    # _env_file=None：不读 backend/.env，测试只认显式入参
    return Settings(_env_file=None, **data)


def test_settings_parses_minimal_valid_env() -> None:
    settings = _settings()
    assert settings.environment == "dev"
    assert settings.api_prefix == "/api/v1"
    assert settings.health_timeout_s > 0
    assert settings.is_prod is False


# --------------------------------------------------------------------------- #
# S3-1：OCR 流水线配置
# --------------------------------------------------------------------------- #
def test_ocr_pipeline_settings_defaults() -> None:
    settings = _settings()
    assert settings.ocr_pipeline_deadline_seconds == 165
    assert settings.ocr_pdf_max_pages == 3
    assert settings.ocr_worker_max_jobs == 1


@pytest.mark.parametrize("value", [0, 3, 10])
def test_ocr_worker_max_jobs_is_capped(value: int) -> None:
    # PaddleOCR 模型非线程安全（AGENTS §5）：只允许 1 或 2
    with pytest.raises(ValidationError):
        _settings(ocr_worker_max_jobs=value)


def test_reread_settings_defaults() -> None:
    settings = _settings()
    assert settings.reread_pad_min_px == 8
    assert settings.reread_pad_ratio == 0.10
    assert settings.reread_scale == 2.0
    assert settings.reread_budget_seconds == 1.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"reread_pad_min_px": -1},
        {"reread_pad_ratio": 1.5},
        {"reread_scale": 0.5},
        {"reread_budget_seconds": 0.0},
    ],
)
def test_reread_settings_are_validated(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        _settings(**overrides)


def test_confidence_settings_defaults() -> None:
    settings = _settings()
    assert (settings.confidence_auto_confirm, settings.confidence_manual_below) == (0.90, 0.60)
    assert settings.confidence_validator_cap == 0.45
    assert settings.confidence_weight_anchor + settings.confidence_weight_value == pytest.approx(1.0)


@pytest.mark.parametrize("missing", ["database_url", "redis_url", "minio_bucket", "jwt_secret"])
def test_settings_missing_required_field_raises(missing: str) -> None:
    data = {key: value for key, value in _BASE.items() if key != missing}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


def test_settings_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValidationError):
        _settings(jwt_secret="too_short")


def test_settings_cors_origins_accepts_comma_separated() -> None:
    settings = _settings(cors_origins="http://a.com, http://b.com")
    assert settings.cors_origins == ["http://a.com", "http://b.com"]


def test_settings_cors_origins_accepts_json_array() -> None:
    settings = _settings(cors_origins='["http://c.com"]')
    assert settings.cors_origins == ["http://c.com"]


def test_settings_rejects_bucket_with_slash() -> None:
    with pytest.raises(ValidationError):
        _settings(minio_bucket="a/b")


def test_get_settings_reports_missing_fields_actionably(monkeypatch, tmp_path) -> None:
    """真实入口 `get_settings()` 缺项时应抛 ConfigError，并指出缺哪个字段、该看哪个文件。"""
    for key in (
        "DATABASE_URL",
        "REDIS_URL",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "JWT_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)  # 确保读不到 backend/.env
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigError) as excinfo:
            get_settings()
        message = str(excinfo.value)
        assert "配置校验失败" in message
        assert "backend/.env" in message
        assert "DATABASE_URL" in message
    finally:
        get_settings.cache_clear()


def test_prod_rejects_weak_jwt_secret(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "prod")
    for key, value in _BASE.items():
        monkeypatch.setenv(key.upper(), value)
    monkeypatch.setenv("JWT_SECRET", "change_me_jwt_secret_at_least_32_chars_long")
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigError, match="prod"):
            get_settings()
    finally:
        get_settings.cache_clear()