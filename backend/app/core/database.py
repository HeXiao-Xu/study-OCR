"""数据库引擎与会话工厂。

为什么需要 URL 映射：
项目装的驱动是 **psycopg 3**（`psycopg[binary]==3.3.9`），但 `.env` 里的 `DATABASE_URL`
是 `postgresql://...`（`/health` 用 psycopg 直连，需要这个形态）。SQLAlchemy 看到
`postgresql://` 会去找 **psycopg2**（未安装），因此统一在 `to_sqlalchemy_url()` 里
改写成 `postgresql+psycopg://`，**不改 .env、不改健康检查**。

S2-1 只建引擎/会话工厂供迁移与后续任务使用，**不接任何路由**（S2-2 起用）。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def to_sqlalchemy_url(database_url: str) -> str:
    """把 psycopg 直连 DSN 映射成 SQLAlchemy 方言；其它协议原样返回。"""
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url


def make_engine(database_url: str | None = None, *, echo: bool = False, **kwargs) -> Engine:
    """创建引擎（惰性连接，调用本函数不会真的连库）。

    集成测试传临时库 URL；业务代码不直接调用，用 `get_engine()`。
    """
    url = to_sqlalchemy_url(database_url or get_settings().database_url)
    options: dict = {"pool_pre_ping": True, "echo": echo}
    options.update(kwargs)
    return create_engine(url, **options)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return make_engine()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """一个用例一个事务：成功提交、失败回滚、最后关闭。

    S2-2 起 service 层通过它拿会话；handler 内不得自行 commit（AGENTS §10.5）。
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 请求级会话依赖：只负责打开/关闭，**事务由 service 决定**。

    不在依赖里自动 commit：注册要写、登录只读，统一提交会把"只读请求"也包进事务，
    并且在路由后期报错时产生"半提交"的错觉。
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()