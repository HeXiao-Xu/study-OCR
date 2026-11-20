"""配置中心。

唯一允许读取环境变量的地方（AGENTS.md §10.2）：业务代码只用 `get_settings()`，
禁止在别处 `os.environ[...]` 或自己再建一份配置。

设计要点：
- 必填项缺失/非法时**启动即失败**并给出清单，而不是等到第一个请求才 500；
- `environment` 决定一些安全默认值（prod 下拒绝弱 JWT_SECRET）；
- 值统一从 `.env`（或进程环境）读，`.env` 不进仓库（见 .gitignore）。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ConfigError(RuntimeError):
    """配置缺失或非法。启动阶段抛出，要求运维/开发者立刻修正。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 应用 ----------
    app_name: str = "study-ocr"
    app_version: str = "0.1.0"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_prefix: str = "/api/v1"
    # CORS：逗号分隔或 JSON 数组；默认只放行本地前端
    # NoDecode：禁止 pydantic-settings 把 dotenv 里的字符串当 JSON 先解析一遍，
    # 否则 .env 里写 `CORS_ORIGINS=http://a,http://b` 会在校验器之前就炸。
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # ---------- 依赖服务（必填，无默认值）----------
    database_url: str
    redis_url: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool = False

    # ---------- 上传与存储（S2-6）----------
    # S4 接入 SM4 信封之前，本地调试允许明文落 MinIO（is_cipher=false）；prod 禁止（见 validator）
    storage_allow_plaintext: bool = True
    upload_max_pdf_pages: int = 5

    # ---------- 安全 ----------
    jwt_secret: str
    max_upload_mb: int = 20  # D-018：单文件上限，前后端共用

    # ---------- 认证（S2-2）----------
    # 算法写死 HS256（API.md §0）；TTL 与 Argon2 参数可调，但默认值进 DECISIONS（D-038）
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_ttl_seconds: int = 1800       # access 30min
    refresh_token_ttl_seconds: int = 604800    # refresh 7d
    refresh_token_prefix: str = "auth:refresh"
    argon2_memory_cost: int = 65536            # 64 MiB，登录耗时的主要来源
    argon2_time_cost: int = 3
    argon2_parallelism: int = 4

    # ---------- OCR（S1-3）----------
    ocr_lang: str = "ch"
    ocr_use_angle_cls: bool = True
    ocr_det_db_thresh: float = 0.3
    ocr_det_db_box_thresh: float = 0.3
    ocr_det_db_unclip_ratio: float = 1.6      # >1.7 票据相邻行会粘连导致识别串行
    ocr_det_limit_side_len: int = 1600        # 与 S1-2 的 warp_max_side 对齐；压测可降 1280
    ocr_device: str = "cpu"
    ocr_model_root: str = ""                  # 空 = ~/.paddleocr；离线机器指向预置目录
    ocr_show_log: bool = False

    # ---------- OCR 流水线（S3-1）----------
    # 协作式 deadline 必须小于 ARQ 的 job_timeout=180，留出写失败状态的余量
    ocr_pipeline_deadline_seconds: int = 165
    ocr_pdf_max_pages: int = 3                 # 任务卡"限前 3 页"
    # PaddleOCR 模型非线程安全（AGENTS §5）；1 是默认，2 只用于 IO/推理重叠对照
    ocr_worker_max_jobs: int = 1

    # ---------- 置信度聚合（S3-2）----------
    # 三档口径：>=auto 免人工；[manual, auto) 待核对；<manual 必须人工（D-058）
    confidence_auto_confirm: float = 0.90
    confidence_manual_below: float = 0.60
    # validators 判错的字段封顶到该值（任务卡"直接 <0.5"）
    confidence_validator_cap: float = 0.45
    # 基础分 = 锚点行 score × w_anchor + 值行 score × w_value（权重和必须为 1）
    confidence_weight_anchor: float = 0.35
    confidence_weight_value: float = 0.65

    # ---------- 局部重识别（S3-3）----------
    # 外扩 = max(PAD_MIN_PX, PAD_RATIO×行高)；放大 SCALE 倍，长边上限 1024
    reread_pad_min_px: int = 8
    reread_pad_ratio: float = 0.10
    reread_scale: float = 2.0
    # 单张票据重识别的总增量预算（API.md §2：≤1.5s，超出记 skipped）
    reread_budget_seconds: float = 1.5

    # ---------- 定时任务（S2-12）----------
    # 每个 API 进程都会起调度器；真正执行前抢 Redis 锁，保证多副本同一次只跑一遍。
    scheduler_enabled: bool = True
    scheduler_timezone: str = "Asia/Shanghai"
    # cron 字段用字符串：默认每日 02:00；双进程验收时可临时设成 "*" / "*/10"
    anchor_cron_hour: str = "2"
    anchor_cron_minute: str = "0"
    anchor_cron_second: str = "0"
    anchor_lock_key: str = "scheduler:lock:anchor_audit"
    anchor_lock_ttl_seconds: int = 3600

    # ---------- 其他 ----------
    health_timeout_s: float = 2.0  # 健康检查单项超时，避免 /health 被拖死
    trace_id_header: str = "X-Trace-Id"

    @field_validator("scheduler_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"未知时区：{value}") from exc
        return value

    @field_validator("anchor_cron_hour", "anchor_cron_minute", "anchor_cron_second")
    @classmethod
    def _check_cron_field(cls, value: str) -> str:
        # 支持 5 / "*/10" / "*"；范围由 APScheduler 再校验一次
        pattern = re.compile(r"^(\*|\*/\d{1,2}|\d{1,2})$")
        if not pattern.match(value):
            raise ValueError(f"cron 字段非法：{value}（只接受数字、*、*/n）")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: object) -> object:
        """支持 `a,b` 这种写法，省得在 .env 里写 JSON。"""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("jwt_secret")
    @classmethod
    def _check_jwt_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET 至少 32 个字符（HS256 的安全下限）")
        return value

    @field_validator("reread_pad_min_px")
    @classmethod
    def _check_reread_pad(cls, value: int) -> int:
        if not 0 <= value <= 200:
            raise ValueError("REREAD_PAD_MIN_PX 必须在 0–200")
        return value

    @field_validator("reread_pad_ratio")
    @classmethod
    def _check_reread_ratio(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("REREAD_PAD_RATIO 必须在 [0,1]")
        return value

    @field_validator("reread_scale")
    @classmethod
    def _check_reread_scale(cls, value: float) -> float:
        if not 1.0 <= value <= 4.0:
            raise ValueError("REREAD_SCALE 必须在 [1,4]（再大只是烧 CPU）")
        return value

    @field_validator("reread_budget_seconds")
    @classmethod
    def _check_reread_budget(cls, value: float) -> float:
        if not 0.1 <= value <= 10.0:
            raise ValueError("REREAD_BUDGET_SECONDS 必须在 [0.1,10]")
        return value

    @field_validator("minio_bucket")
    @classmethod
    def _check_bucket(cls, value: str) -> str:
        if not value or "/" in value:
            raise ValueError("MINIO_BUCKET 不能为空且不能含 '/'")
        return value

    @field_validator("ocr_worker_max_jobs")
    @classmethod
    def _check_worker_jobs(cls, value: int) -> int:
        # 模型非线程安全 + CPU 推理吃满核心：只允许 1 或 2（AGENTS §5）
        if not 1 <= value <= 2:
            raise ValueError("OCR_WORKER_MAX_JOBS 只能是 1 或 2（模型非线程安全）")
        return value

    @model_validator(mode="after")
    def _check_confidence_config(self) -> "Settings":
        weights = (self.confidence_weight_anchor, self.confidence_weight_value)
        if not all(0.0 <= weight <= 1.0 for weight in weights):
            raise ValueError("置信度权重必须在 [0,1]")
        if abs(sum(weights) - 1.0) > 1e-6:
            raise ValueError("置信度权重之和必须为 1")
        if not 0.0 <= self.confidence_validator_cap < self.confidence_manual_below:
            raise ValueError("CONFIDENCE_VALIDATOR_CAP 必须小于 CONFIDENCE_MANUAL_BELOW")
        if not 0.0 <= self.confidence_manual_below < self.confidence_auto_confirm <= 1.0:
            raise ValueError("置信度阈值必须满足 0 <= manual < auto <= 1")
        return self

    @model_validator(mode="after")
    def _check_storage_mode(self) -> "Settings":
        # 明文存储只允许本地调试：prod 必须等 S4 的 SM4 信封接入后走密文
        if self.environment == "prod" and self.storage_allow_plaintext:
            raise ValueError(
                "prod 环境禁止 STORAGE_ALLOW_PLAINTEXT=true：S4 加密链路未接入前只能本地调试明文"
            )
        return self

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


# 生产环境明确禁止的值（示例文件里的占位符）
_WEAK_JWT_SECRETS = {
    "change_me_jwt_secret",
    "change_me_jwt_secret_at_least_32_chars_long",
    "secret",
    "changeme",
    "please_change_me",
}


def _format_validation_error(exc: PydanticValidationError) -> str:
    """把 Pydantic 的报错整理成"缺什么、哪一项不合法"的可读清单。"""
    lines: list[str] = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err.get("loc", ())) or "(root)"
        lines.append(f"  - {field.upper()}: {err.get('msg')}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取并校验配置。缺失/非法直接抛 ConfigError，让进程起不来。"""
    try:
        settings = Settings()
    except PydanticValidationError as exc:
        raise ConfigError(
            "配置校验失败，请检查 backend/.env（可从 backend/.env.example 复制）：\n"
            + _format_validation_error(exc)
        ) from exc

    if settings.is_prod and settings.jwt_secret in _WEAK_JWT_SECRETS:
        raise ConfigError("prod 环境禁止使用示例 JWT_SECRET，请更换为随机值")
    return settings