"""分页、筛选与排序协议测试。

单元部分（默认 `pytest` 跑）：参数边界、白名单、排序方向、`id` 兜底、LIKE 转义。
集成部分（`-m integration`，需 Docker）：真实库翻页/总数/排序/可见性组合/N+1 语句计数/HTTP 422 边界。
"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from decimal import Decimal
from typing import Annotated

import pytest
from alembic import command
from fastapi import Depends, Query
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.exceptions import ValidationFailedError, ok
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.users import User
from app.schemas.common import PageParams
from app.services.pagination import apply_sort, paginate
from app.services.repository import select_visible
from app.utils.text import escape_like
from tests.db_helpers import alembic_config, temporary_database

SORT_MAP = {
    "created_at": Invoice.created_at,
    "invoice_date": Invoice.invoice_date,
    "total_amount": Invoice.total_amount,
}


# --------------------------------------------------------------------------- #
# 单元：PageParams
# --------------------------------------------------------------------------- #
def test_page_params_defaults_and_offset() -> None:
    params = PageParams()
    assert (params.page, params.page_size, params.order) == (1, 20, "desc")
    assert params.offset == 0
    assert PageParams(page=3, page_size=10).offset == 20


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page", 0),
        ("page", -1),
        ("page_size", 0),
        ("page_size", 101),
        ("page_size", 1000),
        ("order", "bogus"),
    ],
)
def test_page_params_reject_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PageParams(**{field: value})


def test_page_params_accept_boundaries() -> None:
    assert PageParams(page=1, page_size=100).page_size == 100
    assert PageParams(page=2, page_size=1).offset == 1


# --------------------------------------------------------------------------- #
# 单元：排序白名单
# --------------------------------------------------------------------------- #
def test_apply_sort_uses_whitelist_and_direction() -> None:
    stmt = apply_sort(
        select(Invoice),
        Invoice,
        SORT_MAP,
        PageParams(sort_by="total_amount", order="asc"),
    )
    sql = str(stmt)
    assert "invoices.total_amount" in sql
    assert "ASC" in sql.upper()
    assert "invoices.id" in sql  # 稳定排序兜底


def test_apply_sort_default_is_applied() -> None:
    stmt = apply_sort(select(Invoice), Invoice, SORT_MAP, PageParams(), default="created_at")
    assert "invoices.created_at" in str(stmt)


def test_apply_sort_rejects_unknown_field() -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        apply_sort(select(Invoice), Invoice, SORT_MAP, PageParams(sort_by="drop table"))
    assert "sort_by" in excinfo.value.message
    assert "created_at" in excinfo.value.message


def test_apply_sort_requires_default_when_sort_by_missing() -> None:
    with pytest.raises(ValidationFailedError):
        apply_sort(select(Invoice), Invoice, SORT_MAP, PageParams())


# --------------------------------------------------------------------------- #
# 单元：LIKE 转义
# --------------------------------------------------------------------------- #
def test_escape_like_escapes_wildcards() -> None:
    assert escape_like("a%b_c\\d") == "%a\\%b\\_c\\\\d%"
    assert escape_like("发票 123") == "%发票 123%"


# --------------------------------------------------------------------------- #
# 集成夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def pagination_database() -> Iterator[URL]:
    with temporary_database("study_ocr_page_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(pagination_database: URL):
    eng = create_engine(
        pagination_database.render_as_string(hide_password=False), poolclass=NullPool
    )
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def clean_db(engine):
    with engine.begin() as conn:
        conn.execute(sa_text("TRUNCATE users RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def actors(session_factory, clean_db) -> dict[str, User]:
    with session_factory() as db:
        users = {
            "alice": User(username="alice", real_name="A", role="submitter", status="active"),
            "bob": User(username="bob", real_name="B", role="submitter", status="active"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


@pytest.fixture()
def client(session_factory, clean_db) -> Iterator[TestClient]:
    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db

    @app.get("/api/v1/_test/page")
    def _test_page(
        params: Annotated[PageParams, Query()],
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        stmt = select_visible(Invoice, user)
        result = paginate(db, stmt, Invoice, SORT_MAP, params, default="created_at")
        return ok(
            {
                "items": [{"id": row.id} for row in result.items],
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
            }
        )

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _seed_invoice(
    session_factory,
    uploader_id: int,
    *,
    status: str = "draft",
    total: Decimal = Decimal("0.00"),
) -> int:
    with session_factory() as db:
        file = InvoiceFile(
            uploader_id=uploader_id,
            filename="a.jpg",
            mime="image/jpeg",
            size=1024,
            sha256=secrets.token_hex(32),
            storage_key=f"invoices/{secrets.token_hex(8)}.bin",
        )
        db.add(file)
        db.flush()
        invoice = Invoice(
            uploader_id=uploader_id,
            file_id=file.id,
            status=status,
            total_amount=total,
        )
        db.add(invoice)
        db.commit()
        return invoice.id


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(
        user_id=user.id,
        role=user.role,
        token_type=TokenType.ACCESS,
        ttl_seconds=60,
    )
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# 集成：翻页正确性
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_pages_partition_and_beyond_last_is_empty(session_factory, actors) -> None:
    ids = {_seed_invoice(session_factory, actors["alice"].id) for _ in range(25)}

    with session_factory() as db:
        user = db.get(User, actors["alice"].id)
        stmt = select_visible(Invoice, user)
        seen: list[int] = []
        for page in (1, 2, 3):
            result = paginate(
                db, stmt, Invoice, SORT_MAP, PageParams(page=page, page_size=10),
                default="created_at",
            )
            assert result.total == 25
            assert len(result.items) == (10 if page < 3 else 5)
            seen.extend(row.id for row in result.items)

        assert len(seen) == len(set(seen)) == 25
        assert set(seen) == ids

        beyond = paginate(
            db, stmt, Invoice, SORT_MAP, PageParams(page=4, page_size=10),
            default="created_at",
        )
        assert beyond.items == []
        assert beyond.total == 25


@pytest.mark.integration
def test_count_uses_the_same_filter(session_factory, actors) -> None:
    for _ in range(10):
        _seed_invoice(session_factory, actors["alice"].id, status="confirmed")
    for _ in range(15):
        _seed_invoice(session_factory, actors["alice"].id, status="draft")

    with session_factory() as db:
        user = db.get(User, actors["alice"].id)
        stmt = select_visible(Invoice, user).where(Invoice.status == "confirmed")
        result = paginate(
            db, stmt, Invoice, SORT_MAP, PageParams(page=1, page_size=20),
            default="created_at",
        )
        assert result.total == 10
        assert len(result.items) == 10


@pytest.mark.integration
def test_sort_direction_is_applied(session_factory, actors) -> None:
    for value in (1, 2, 3, 4, 5):
        _seed_invoice(
            session_factory,
            actors["alice"].id,
            total=Decimal(str(value)),
        )

    with session_factory() as db:
        user = db.get(User, actors["alice"].id)
        stmt = select_visible(Invoice, user)

        ascending = paginate(
            db, stmt, Invoice, SORT_MAP,
            PageParams(page=1, page_size=5, sort_by="total_amount", order="asc"),
        )
        descending = paginate(
            db, stmt, Invoice, SORT_MAP,
            PageParams(page=1, page_size=5, sort_by="total_amount", order="desc"),
        )
        assert [row.total_amount for row in ascending.items] == [
            Decimal(v) for v in ("1.00", "2.00", "3.00", "4.00", "5.00")
        ]
        assert [row.total_amount for row in descending.items] == [
            Decimal(v) for v in ("5.00", "4.00", "3.00", "2.00", "1.00")
        ]


@pytest.mark.integration
def test_pagination_respects_row_level_visibility(session_factory, actors) -> None:
    for _ in range(3):
        _seed_invoice(session_factory, actors["alice"].id)
    for _ in range(2):
        _seed_invoice(session_factory, actors["bob"].id)

    with session_factory() as db:
        alice = db.get(User, actors["alice"].id)
        rita = db.get(User, actors["rita"].id)

        alice_page = paginate(
            db, select_visible(Invoice, alice), Invoice, SORT_MAP,
            PageParams(page=1, page_size=20), default="created_at",
        )
        reviewer_page = paginate(
            db, select_visible(Invoice, rita), Invoice, SORT_MAP,
            PageParams(page=1, page_size=20), default="created_at",
        )

    assert alice_page.total == 3
    assert reviewer_page.total == 5


@pytest.mark.integration
def test_join_list_uses_two_queries(engine, session_factory, actors) -> None:
    for _ in range(5):
        _seed_invoice(session_factory, actors["alice"].id)

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    with session_factory() as db:
        user = db.get(User, actors["alice"].id)
        stmt = (
            select_visible(Invoice, user)
            .join(InvoiceFile, Invoice.file_id == InvoiceFile.id)
            .add_columns(InvoiceFile.filename, InvoiceFile.sha256)
        )

        event.listen(engine, "before_cursor_execute", _record)
        try:
            result = paginate(
                db, stmt, Invoice, SORT_MAP, PageParams(page=1, page_size=5),
                default="created_at",
                scalar=False,  # join + add_columns → items 是 Row
            )
        finally:
            event.remove(engine, "before_cursor_execute", _record)

        assert len(result.items) == 5
        # 每行都带出文件字段，没有逐行回查
        assert all(row.filename == "a.jpg" for row in result.items)

    # count + 数据，共 2 条 SELECT；N+1 会随行数增长
    assert len(statements) == 2


# --------------------------------------------------------------------------- #
# 集成：HTTP 边界（真实 PageParams 解析 + 统一错误壳）
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.parametrize(
    "query",
    ["page=0", "page=-1", "page_size=0", "page_size=101", "page_size=1000", "order=bogus"],
)
def test_http_rejects_invalid_page_params(client, actors, query: str) -> None:
    response = client.get(f"/api/v1/_test/page?{query}", headers=_auth(actors["alice"]))
    assert response.status_code == 422
    assert response.json()["code"] == 42200


@pytest.mark.integration
def test_http_returns_page_shape(client, actors, session_factory) -> None:
    seed_ids = {_seed_invoice(session_factory, actors["alice"].id) for _ in range(3)}

    response = client.get(
        "/api/v1/_test/page?page=1&page_size=2&sort_by=created_at&order=desc",
        headers=_auth(actors["alice"]),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"items", "total", "page", "page_size"}
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert len(data["items"]) == 2
    assert {item["id"] for item in data["items"]} <= seed_ids


@pytest.mark.integration
def test_http_rejects_unknown_sort_field(client, actors) -> None:
    response = client.get(
        "/api/v1/_test/page?sort_by=total_amount;drop%20table",
        headers=_auth(actors["alice"]),
    )
    assert response.status_code == 422
    assert response.json()["code"] == 42200
    assert "created_at" in response.json()["message"]