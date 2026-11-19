"""集成测试的数据库助手：临时库、Alembic 配置、表清单。

为什么不用开发库：集成测试要跑迁移、写数据，绝不能污染 `studyocr`。
每个测试模块用一个 `study_ocr_<用途>_<hex>` 临时库，`finally` 删除。
"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from app.core.database import to_sqlalchemy_url

BACKEND = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND / ".env"


def database_url_from_env() -> URL:
    """从 backend/.env 读真实连接串（conftest 注入的是假凭据，不能用于真连）。"""
    if not ENV_FILE.exists():
        pytest.skip("缺少 backend/.env，无法拿到本地数据库连接")
    values = dict(
        line.split("=", 1)
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    )
    raw = values.get("DATABASE_URL", "").strip()
    if not raw:
        pytest.skip("backend/.env 未配置 DATABASE_URL")
    return make_url(to_sqlalchemy_url(raw))


def _admin_engine(url: URL):
    return create_engine(
        url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )


@contextmanager
def temporary_database(prefix: str = "study_ocr_test_") -> Iterator[URL]:
    """在本地 PG 上建一个一次性库，退出时删除（删不掉只告警）。"""
    url = database_url_from_env()
    name = f"{prefix}{secrets.token_hex(4)}"
    admin = _admin_engine(url)
    try:
        try:
            with admin.connect() as conn:
                conn.execute(sa_text(f'CREATE DATABASE "{name}"'))
        except Exception as exc:  # noqa: BLE001 - 连不上就跳过，不制造假绿
            pytest.skip(f"本地 PostgreSQL 不可用，跳过集成测试：{exc}")
        yield url.set(database=name)
    finally:
        admin.dispose()
        cleanup = _admin_engine(url)
        try:
            with cleanup.connect() as conn:
                conn.execute(sa_text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响结论
            print(f"[warn] 临时库 {name} 清理失败：{exc}")
        finally:
            cleanup.dispose()


def alembic_config(url: URL) -> AlembicConfig:
    cfg = AlembicConfig(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    # configparser 插值：URL 里若有 % 必须转义
    cfg.set_main_option(
        "sqlalchemy.url",
        url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    return cfg


def list_tables(url: URL) -> set[str]:
    engine = create_engine(url.render_as_string(hide_password=False), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return set(
                conn.execute(
                    sa_text(
                        "select table_name from information_schema.tables"
                        " where table_schema = 'public'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()