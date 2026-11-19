"""Alembic 运行环境。

三个要点：
1. **URL 不落盘**：ini 里留空，这里从 Settings 读 `DATABASE_URL` 再映射成
   `postgresql+psycopg://`；集成测试可用 `config.set_main_option("sqlalchemy.url", ...)`
   注入临时库，优先于 Settings。
2. **显式补 backend/ 到 sys.path**：不依赖运行 `alembic` 时的 cwd
   （从仓库根跑也能 import 到 `app.*`）。
3. `target_metadata` 只来自 `app.models`：`models/__init__.py` 必须导入全部 16 表，
   否则 autogenerate 会认为"这些表该删掉"。
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.core.database import to_sqlalchemy_url  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False：不能把应用已配置的 logger 关掉
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """调用方注入的 URL 优先（集成测试），否则读 Settings。"""
    configured = (config.get_main_option("sqlalchemy.url") or "").strip()
    raw = configured or get_settings().database_url
    return to_sqlalchemy_url(raw)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()