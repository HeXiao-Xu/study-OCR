"""`app.core.database` 的纯函数单测（不连库）。"""
from __future__ import annotations

from app.core.database import make_engine, to_sqlalchemy_url


def test_to_sqlalchemy_url_maps_psycopg_dsn() -> None:
    assert (
        to_sqlalchemy_url("postgresql://u:p@127.0.0.1:5433/db")
        == "postgresql+psycopg://u:p@127.0.0.1:5433/db"
    )


def test_to_sqlalchemy_url_maps_legacy_scheme() -> None:
    assert to_sqlalchemy_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_to_sqlalchemy_url_keeps_existing_dialect() -> None:
    url = "postgresql+psycopg://u:p@h/db"
    assert to_sqlalchemy_url(url) == url
    assert to_sqlalchemy_url("sqlite:///tmp.db") == "sqlite:///tmp.db"


def test_make_engine_does_not_connect_and_uses_psycopg() -> None:
    engine = make_engine("postgresql://u:p@127.0.0.1:1/none")
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.database == "none"
        # pool_pre_ping 打开：连接池取到坏连接时先探活，避免"偶发 500"
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()