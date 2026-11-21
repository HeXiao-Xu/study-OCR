"""行级数据隔离的集成测试（临时 PostgreSQL 库；默认跳过，`-m integration` 跑）。

覆盖 API.md §0 的权限矩阵：
- submitter 只能读自己的票据 / 文件 / 报销单；
- reviewer 能读全部票据与文件，但报销单只看到 `submitted` 之后；
- admin 对业务表一律 404；
- 列表与详情共用同一谓词；HTTP 层越权是 404/40400（不是 403）。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from collections.abc import Iterator

import pytest
from alembic import command
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.exceptions import NotFoundError, PermissionDeniedError, ok
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.enums import ReportStatus
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.users import User
from app.services.repository import (
    REVIEWER_REPORT_STATUSES,
    get_visible_or_404,
    select_visible,
)
from tests.db_helpers import alembic_config, temporary_database

pytestmark = pytest.mark.integration

HIDDEN_REPORT_STATUSES = ("draft", "pending_sign", "signed", "encrypted")


# --------------------------------------------------------------------------- #
# 夹具：临时库 + 角色 + 测试专用路由
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def isolation_database() -> Iterator[URL]:
    with temporary_database("study_ocr_iso_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(isolation_database: URL):
    eng = create_engine(
        isolation_database.render_as_string(hide_password=False), poolclass=NullPool
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
    # CASCADE 会连带清空 invoices/invoice_files/report_forms 等引用表
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
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        # sessionmaker 配置 expire_on_commit=False，detached 后 id/role 仍可读
        return users


@pytest.fixture()
def client(engine, session_factory, clean_db) -> Iterator[TestClient]:
    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db

    # 仅测试用：证明生产异常处理器把"存在但无权"也转成 404/40400
    @app.get("/api/v1/_test/invoices/{invoice_id}")
    def _test_detail(
        invoice_id: int,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        invoice = get_visible_or_404(db, Invoice, invoice_id, user)
        return ok({"id": invoice.id})

    @app.get("/api/v1/_test/invoices")
    def _test_list(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        rows = db.scalars(select_visible(Invoice, user)).all()
        return ok({"ids": sorted(row.id for row in rows)})

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 数据助手
# --------------------------------------------------------------------------- #
def _invoice(session_factory, uploader_id: int) -> int:
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
        invoice = Invoice(uploader_id=uploader_id, file_id=file.id)
        db.add(invoice)
        db.commit()
        return invoice.id


def _report(session_factory, applicant_id: int, status: str) -> int:
    with session_factory() as db:
        report = ReportForm(
            report_no=f"RPT-{secrets.token_hex(4)}",
            applicant_id=applicant_id,
            title="测试报销单",
            status=status,
            archive_no=f"AR-TEST-{secrets.token_hex(4)}" if status == "archived" else None,
            archive_at=datetime.now(timezone.utc) if status == "archived" else None,
        )
        db.add(report)
        db.commit()
        return report.id


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(
        user_id=user.id,
        role=user.role,
        token_type=TokenType.ACCESS,
        ttl_seconds=60,
    )
    return {"Authorization": f"Bearer {token}"}


def _visible_ids(db: Session, model, user: User) -> set[int]:
    return {row.id for row in db.scalars(select_visible(model, user))}


# --------------------------------------------------------------------------- #
# 服务层：票据与文件
# --------------------------------------------------------------------------- #
def test_submitter_list_sees_only_own_invoices(session_factory, actors) -> None:
    alice_invoice = _invoice(session_factory, actors["alice"].id)
    bob_invoice = _invoice(session_factory, actors["bob"].id)

    with session_factory() as db:
        assert _visible_ids(db, Invoice, actors["alice"]) == {alice_invoice}
        assert _visible_ids(db, Invoice, actors["bob"]) == {bob_invoice}


def test_submitter_detail_of_others_is_denied_internally(session_factory, actors) -> None:
    alice_invoice = _invoice(session_factory, actors["alice"].id)

    with session_factory() as db:
        # 存在但无权 → PermissionDeniedError（对外仍 404）
        with pytest.raises(PermissionDeniedError):
            get_visible_or_404(db, Invoice, alice_invoice, actors["bob"])
        # 确实不存在 → NotFoundError
        with pytest.raises(NotFoundError):
            get_visible_or_404(db, Invoice, 999999, actors["bob"])
        # 本人可以看
        assert get_visible_or_404(db, Invoice, alice_invoice, actors["alice"]).id == alice_invoice


def test_reviewer_sees_all_invoices(session_factory, actors) -> None:
    alice_invoice = _invoice(session_factory, actors["alice"].id)
    bob_invoice = _invoice(session_factory, actors["bob"].id)

    with session_factory() as db:
        assert _visible_ids(db, Invoice, actors["rita"]) == {alice_invoice, bob_invoice}
        assert get_visible_or_404(db, Invoice, alice_invoice, actors["rita"]).id == alice_invoice


def test_admin_has_no_row_level_access_to_invoices(session_factory, actors) -> None:
    alice_invoice = _invoice(session_factory, actors["alice"].id)

    with session_factory() as db:
        assert _visible_ids(db, Invoice, actors["ada"]) == set()
        with pytest.raises(PermissionDeniedError):
            get_visible_or_404(db, Invoice, alice_invoice, actors["ada"])


def test_invoice_file_isolation(session_factory, actors) -> None:
    alice_invoice = _invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        file_id = db.get(Invoice, alice_invoice).file_id

        assert get_visible_or_404(db, InvoiceFile, file_id, actors["alice"]).id == file_id
        with pytest.raises(PermissionDeniedError):
            get_visible_or_404(db, InvoiceFile, file_id, actors["bob"])
        assert get_visible_or_404(db, InvoiceFile, file_id, actors["rita"]).id == file_id
        with pytest.raises(PermissionDeniedError):
            get_visible_or_404(db, InvoiceFile, file_id, actors["ada"])


# --------------------------------------------------------------------------- #
# 服务层：报销单
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "status",
    [status.value for status in ReportStatus],
)
def test_reviewer_report_visibility_follows_status(session_factory, actors, status: str) -> None:
    report_id = _report(session_factory, actors["alice"].id, status)
    visible = status in REVIEWER_REPORT_STATUSES

    with session_factory() as db:
        assert (report_id in _visible_ids(db, ReportForm, actors["rita"])) is visible
        if visible:
            assert get_visible_or_404(db, ReportForm, report_id, actors["rita"]).id == report_id
        else:
            with pytest.raises(PermissionDeniedError):
                get_visible_or_404(db, ReportForm, report_id, actors["rita"])


def test_submitter_sees_own_drafts_but_admin_does_not(session_factory, actors) -> None:
    draft = _report(session_factory, actors["alice"].id, "draft")

    with session_factory() as db:
        assert draft in _visible_ids(db, ReportForm, actors["alice"])
        assert draft not in _visible_ids(db, ReportForm, actors["bob"])
        assert draft not in _visible_ids(db, ReportForm, actors["ada"])
        with pytest.raises(PermissionDeniedError):
            get_visible_or_404(db, ReportForm, draft, actors["ada"])


# --------------------------------------------------------------------------- #
# 列表与详情一致性
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("actor_name", ["alice", "bob", "rita", "ada"])
def test_list_and_detail_agree_for_every_role(session_factory, actors, actor_name: str) -> None:
    ids = [
        _invoice(session_factory, actors["alice"].id),
        _invoice(session_factory, actors["bob"].id),
    ]
    actor = actors[actor_name]

    with session_factory() as db:
        listed = _visible_ids(db, Invoice, actor)
        for invoice_id in ids:
            try:
                get_visible_or_404(db, Invoice, invoice_id, actor)
                detail_ok = True
            except (NotFoundError, PermissionDeniedError):
                detail_ok = False
            assert detail_ok is (invoice_id in listed)


# --------------------------------------------------------------------------- #
# HTTP 层（真实 create_app + 异常处理器）
# --------------------------------------------------------------------------- #
def test_http_other_submitter_gets_404_not_403(client, actors, session_factory) -> None:
    alice_invoice = _invoice(session_factory, actors["alice"].id)

    denied = client.get(f"/api/v1/_test/invoices/{alice_invoice}", headers=_auth(actors["bob"]))
    assert denied.status_code == 404
    assert denied.json()["code"] == 40400

    allowed = client.get(f"/api/v1/_test/invoices/{alice_invoice}", headers=_auth(actors["alice"]))
    assert allowed.status_code == 200
    assert allowed.json()["data"]["id"] == alice_invoice


def test_http_reviewer_can_read_and_admin_cannot(client, actors, session_factory) -> None:
    alice_invoice = _invoice(session_factory, actors["alice"].id)

    assert (
        client.get(f"/api/v1/_test/invoices/{alice_invoice}", headers=_auth(actors["rita"])).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/_test/invoices/{alice_invoice}", headers=_auth(actors["ada"])).status_code
        == 404
    )


def test_http_list_filters_rows(client, actors, session_factory) -> None:
    alice_invoice = _invoice(session_factory, actors["alice"].id)

    bob_list = client.get("/api/v1/_test/invoices", headers=_auth(actors["bob"]))
    assert bob_list.status_code == 200
    assert alice_invoice not in bob_list.json()["data"]["ids"]

    alice_list = client.get("/api/v1/_test/invoices", headers=_auth(actors["alice"]))
    assert alice_list.json()["data"]["ids"] == [alice_invoice]

    reviewer_list = client.get("/api/v1/_test/invoices", headers=_auth(actors["rita"]))
    assert alice_invoice in reviewer_list.json()["data"]["ids"]

    admin_list = client.get("/api/v1/_test/invoices", headers=_auth(actors["ada"]))
    assert admin_list.json()["data"]["ids"] == []


def test_http_missing_invoice_returns_404(client, actors) -> None:
    response = client.get("/api/v1/_test/invoices/999999", headers=_auth(actors["alice"]))
    assert response.status_code == 404
    assert response.json()["code"] == 40400


def test_http_requires_login(client) -> None:
    response = client.get("/api/v1/_test/invoices")
    assert response.status_code == 401
    assert response.json()["code"] == 40101