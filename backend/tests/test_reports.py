"""S5-1 报销单创建与明细：建单/明细/金额/权限/并发/回滚/状态机（需 Docker PostgreSQL）。

跑法：`cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_reports.py -m integration -q`

并发用例用**独立 Session + 线程**直接调 service：TestClient 的网络栈不保证真并发，
而本卡的核心风险恰恰是"并发建单重号"与"同一票进两张单"。
"""
from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.audit_logs import AuditLog
from app.models.enums import InvoiceStatus, ReportStatus
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.report_items import ReportItem
from app.models.users import User
from app.schemas.report import ReportCreateRequest, ReportItemCreateRequest
from app.services import report_service
from app.services.audit import verify_chain
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/reports"
pytestmark = pytest.mark.integration

REPORT_NO_RE = re.compile(r"^RPT-\d{6}-\d{4,}$")


# --------------------------------------------------------------------------- #
# 装置
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def reports_database() -> Iterator[URL]:
    with temporary_database("study_ocr_reports_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(reports_database: URL):
    eng = create_engine(
        reports_database.render_as_string(hide_password=False), poolclass=NullPool
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
    # users 级联到 invoices / report_forms / report_items / audit_logs
    with engine.begin() as conn:
        conn.execute(sa_text("TRUNCATE users RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def actors(session_factory, clean_db) -> dict[str, User]:
    with session_factory() as db:
        users = {
            "alice": User(
                username="alice", real_name="A", role="submitter", status="active",
                dept="计算机学院",
            ),
            "bob": User(username="bob", real_name="B", role="submitter", status="active",
                        dept="外国语学院"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
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
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(
        user_id=user.id, role=user.role, token_type=TokenType.ACCESS, ttl_seconds=60
    )
    return {"Authorization": f"Bearer {token}"}


def _confirmed_invoice(
    session_factory, owner: User, *, total: str = "113.00",
    status: str = InvoiceStatus.CONFIRMED.value,
) -> int:
    """直接落一张已确认票据（本卡不测 OCR/确认链路，避免依赖 S3 的夹具）。"""
    with session_factory() as db:
        file_row = InvoiceFile(
            uploader_id=owner.id,
            filename="a.jpg",
            mime="image/jpeg",
            size=1024,
            sha256=secrets.token_hex(32),
            storage_key=f"invoices/2026/11/{secrets.token_hex(8)}.bin",
            page_count=1,
        )
        db.add(file_row)
        db.flush()
        invoice = Invoice(
            uploader_id=owner.id,
            file_id=file_row.id,
            invoice_type="vat_general",
            status=status,
            ocr_status="succeeded",
            extract_status="confirmed",
            amount=Decimal(total),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal(total),
        )
        db.add(invoice)
        db.commit()
        return invoice.id


def _create_report(client: TestClient, user: User, **overrides) -> dict:
    payload = {"title": "2026-11 差旅报销", "reason": "学术会议差旅"}
    payload.update(overrides)
    response = client.post(BASE, json=payload, headers=_auth(user))
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _form_row(session_factory, report_id: int) -> ReportForm:
    with session_factory() as db:
        return db.get(ReportForm, report_id)


def _invoice_row(session_factory, invoice_id: int) -> Invoice:
    with session_factory() as db:
        return db.get(Invoice, invoice_id)


# --------------------------------------------------------------------------- #
# 建单
# --------------------------------------------------------------------------- #
def test_create_report_returns_draft(client, actors) -> None:
    data = _create_report(client, actors["alice"])

    assert REPORT_NO_RE.match(data["report_no"]), data["report_no"]
    assert data["status"] == ReportStatus.DRAFT.value
    assert data["total_amount"] == "0.00"
    assert data["item_count"] == 0
    assert data["items"] == []
    assert data["applicant_id"] == actors["alice"].id
    # department 是派生字段（users.dept），不是 report_forms 的列
    assert data["department"] == "计算机学院"


def test_create_report_writes_audit_row(client, session_factory, actors) -> None:
    data = _create_report(client, actors["alice"])

    with session_factory() as db:
        rows = db.scalars(
            select(AuditLog).where(AuditLog.action == "report_create")
        ).all()
        assert len(rows) == 1
        assert rows[0].target_type == "form"
        assert rows[0].target_id == data["id"]
        assert rows[0].actor_id == actors["alice"].id
        assert rows[0].actor_role == "submitter"
        # 只记单号与状态：不写标题/事由/金额
        assert rows[0].detail_json == {
            "report_no": data["report_no"],
            "status": "draft",
        }
        assert verify_chain(db).ok is True


def test_create_report_validation(client, actors) -> None:
    for payload in (
        {"title": "", "reason": "x"},
        {"title": "x" * 101, "reason": "x"},
        {"title": "ok", "reason": ""},
        {"title": "ok", "reason": "x" * 501},
    ):
        response = client.post(BASE, json=payload, headers=_auth(actors["alice"]))
        assert response.status_code == 422, (payload, response.text)
        assert response.json()["code"] == 42200


def test_create_report_roles(client, actors) -> None:
    payload = {"title": "t", "reason": "r"}
    reviewer = client.post(BASE, json=payload, headers=_auth(actors["rita"]))
    admin = client.post(BASE, json=payload, headers=_auth(actors["ada"]))
    assert reviewer.status_code == 403 and reviewer.json()["code"] == 40301
    assert admin.status_code == 404 and admin.json()["code"] == 40400
    assert client.post(BASE, json=payload).status_code == 401


def test_report_numbers_are_unique_across_calls(client, actors) -> None:
    numbers = {_create_report(client, actors["alice"])["report_no"] for _ in range(3)}
    assert len(numbers) == 3


# --------------------------------------------------------------------------- #
# 加明细
# --------------------------------------------------------------------------- #
def test_add_three_items(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    invoices = [
        _confirmed_invoice(session_factory, actors["alice"], total=total)
        for total in ("100.00", "150.00", "80.50")
    ]

    data = None
    for invoice_id, amount in zip(invoices, ("100.00", "150.00", "80.50")):
        response = client.post(
            f"{BASE}/{report['id']}/items",
            json={"invoice_id": invoice_id, "amount": amount, "remark": "交通"},
            headers=_auth(actors["alice"]),
        )
        assert response.status_code == 201, response.text
        data = response.json()["data"]

    assert data["total_amount"] == "330.50"       # Decimal 汇总，无 0.01 漂移
    assert data["item_count"] == 3
    assert [item["amount"] for item in data["items"]] == ["100.00", "150.00", "80.50"]
    assert data["items"][0]["remark"] == "交通"

    for invoice_id in invoices:
        invoice = _invoice_row(session_factory, invoice_id)
        assert invoice.status == InvoiceStatus.IN_REPORT.value
        assert invoice.version == 1


def test_add_item_zero_amount_allowed(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    invoice_id = _confirmed_invoice(session_factory, actors["alice"], total="50.00")

    response = client.post(
        f"{BASE}/{report['id']}/items",
        json={"invoice_id": invoice_id, "amount": "0.00"},
        headers=_auth(actors["alice"]),
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["total_amount"] == "0.00" and data["item_count"] == 1


def test_add_item_over_total_returns_42201(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    invoice_id = _confirmed_invoice(session_factory, actors["alice"], total="100.00")

    response = client.post(
        f"{BASE}/{report['id']}/items",
        json={"invoice_id": invoice_id, "amount": "100.01"},
        headers=_auth(actors["alice"]),
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == 42201
    assert body["data"]["expected"] == "100.00"
    assert body["data"]["actual"] == "100.01"


def test_add_item_malformed_amounts(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    invoice_id = _confirmed_invoice(session_factory, actors["alice"], total="100.00")

    for amount in ("-1.00", "1.234", "abc"):
        response = client.post(
            f"{BASE}/{report['id']}/items",
            json={"invoice_id": invoice_id, "amount": amount},
            headers=_auth(actors["alice"]),
        )
        assert response.status_code == 422, (amount, response.text)
        assert response.json()["code"] == 42200


def test_add_item_rejects_other_users_invoice(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    bobs_invoice = _confirmed_invoice(session_factory, actors["bob"], total="100.00")

    response = client.post(
        f"{BASE}/{report['id']}/items",
        json={"invoice_id": bobs_invoice, "amount": "10.00"},
        headers=_auth(actors["alice"]),
    )

    # 非本人与不存在同响应：不泄露票据是否存在
    assert response.status_code == 404 and response.json()["code"] == 40400
    assert _invoice_row(session_factory, bobs_invoice).status == InvoiceStatus.CONFIRMED.value


def test_add_item_rejects_unconfirmed_invoice(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    draft_invoice = _confirmed_invoice(
        session_factory, actors["alice"], total="100.00", status=InvoiceStatus.DRAFT.value
    )

    response = client.post(
        f"{BASE}/{report['id']}/items",
        json={"invoice_id": draft_invoice, "amount": "10.00"},
        headers=_auth(actors["alice"]),
    )

    assert response.status_code == 409 and response.json()["code"] == 40900


def test_add_same_invoice_twice_returns_40901(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    invoice_id = _confirmed_invoice(session_factory, actors["alice"], total="100.00")
    body = {"invoice_id": invoice_id, "amount": "10.00"}

    first = client.post(f"{BASE}/{report['id']}/items", json=body, headers=_auth(actors["alice"]))
    second = client.post(f"{BASE}/{report['id']}/items", json=body, headers=_auth(actors["alice"]))

    assert first.status_code == 201, first.text
    assert second.status_code == 409 and second.json()["code"] == 40901
    with session_factory() as db:
        count = db.scalar(
            select(func.count()).select_from(ReportItem).where(
                ReportItem.form_id == report["id"]
            )
        )
        assert count == 1


def test_add_invoice_already_in_other_form_returns_40901(
    client, session_factory, actors
) -> None:
    first = _create_report(client, actors["alice"])
    second = _create_report(client, actors["alice"])
    invoice_id = _confirmed_invoice(session_factory, actors["alice"], total="100.00")

    assert client.post(
        f"{BASE}/{first['id']}/items",
        json={"invoice_id": invoice_id, "amount": "10.00"},
        headers=_auth(actors["alice"]),
    ).status_code == 201

    response = client.post(
        f"{BASE}/{second['id']}/items",
        json={"invoice_id": invoice_id, "amount": "10.00"},
        headers=_auth(actors["alice"]),
    )

    assert response.status_code == 409 and response.json()["code"] == 40901
    assert _form_row(session_factory, second["id"]).item_count == 0
    assert _invoice_row(session_factory, invoice_id).status == InvoiceStatus.IN_REPORT.value


def test_add_item_rejects_non_draft_form(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    invoice_id = _confirmed_invoice(session_factory, actors["alice"], total="100.00")
    with session_factory() as db:
        db.execute(
            sa_text("update report_forms set status = 'pending_sign' where id = :id"),
            {"id": report["id"]},
        )
        db.commit()

    response = client.post(
        f"{BASE}/{report['id']}/items",
        json={"invoice_id": invoice_id, "amount": "10.00"},
        headers=_auth(actors["alice"]),
    )

    assert response.status_code == 409 and response.json()["code"] == 40900


def test_failed_add_rolls_back_invoice_and_form(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    invoice_id = _confirmed_invoice(session_factory, actors["alice"], total="10.00")

    response = client.post(
        f"{BASE}/{report['id']}/items",
        json={"invoice_id": invoice_id, "amount": "10.01"},
        headers=_auth(actors["alice"]),
    )
    assert response.status_code == 422

    assert _invoice_row(session_factory, invoice_id).status == InvoiceStatus.CONFIRMED.value
    form = _form_row(session_factory, report["id"])
    assert (form.total_amount, form.item_count, form.version) == (Decimal("0.00"), 0, 0)
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ReportItem)) == 0


# --------------------------------------------------------------------------- #
# 并发
# --------------------------------------------------------------------------- #
def test_concurrent_creates_have_distinct_report_numbers(session_factory, actors) -> None:
    total = 10
    barrier = Barrier(total)

    def _create(_: int) -> str:
        db = session_factory()
        try:
            user = db.get(User, actors["alice"].id)
            barrier.wait(timeout=10)
            report = report_service.create_report(
                db, user, ReportCreateRequest(title="并发建单", reason="并发测试")
            )
            return report.report_no
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=total) as pool:
        numbers = list(pool.map(_create, range(total)))

    assert len(set(numbers)) == total
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ReportForm)) == total


def test_concurrent_add_same_invoice_only_one_wins(client, session_factory, actors) -> None:
    first = _create_report(client, actors["alice"])
    second = _create_report(client, actors["alice"])
    invoice_id = _confirmed_invoice(session_factory, actors["alice"], total="100.00")
    barrier = Barrier(2)

    def _add(report_id: int) -> tuple[str, int | None]:
        db = session_factory()
        try:
            user = db.get(User, actors["alice"].id)
            barrier.wait(timeout=10)
            report_service.add_item(
                db, user, report_id,
                ReportItemCreateRequest(invoice_id=invoice_id, amount=Decimal("10.00")),
            )
            return ("ok", None)
        except AppError as exc:
            return ("error", exc.code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_add, [first["id"], second["id"]]))

    assert sorted(status for status, _ in results) == ["error", "ok"]
    assert [code for status, code in results if status == "error"] == [40901]
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ReportItem)) == 1
    assert _invoice_row(session_factory, invoice_id).status == InvoiceStatus.IN_REPORT.value


def test_concurrent_add_same_form_keeps_totals_consistent(
    client, session_factory, actors
) -> None:
    """同一单据并发加**不同**票据：单据行锁必须让合计/条数不丢更新。"""
    report = _create_report(client, actors["alice"])
    invoices = [
        _confirmed_invoice(session_factory, actors["alice"], total="100.00"),
        _confirmed_invoice(session_factory, actors["alice"], total="50.00"),
    ]
    barrier = Barrier(2)

    def _add(invoice_id: int) -> tuple[str, int | None]:
        db = session_factory()
        try:
            user = db.get(User, actors["alice"].id)
            barrier.wait(timeout=10)
            report_service.add_item(
                db, user, report["id"],
                ReportItemCreateRequest(invoice_id=invoice_id, amount=Decimal("10.00")),
            )
            return ("ok", None)
        except AppError as exc:
            return ("error", exc.code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_add, invoices))

    assert [status for status, _ in results] == ["ok", "ok"]
    form = _form_row(session_factory, report["id"])
    assert (form.item_count, form.total_amount) == (2, Decimal("20.00"))
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ReportItem)) == 2


# --------------------------------------------------------------------------- #
# 详情 / 我的列表
# --------------------------------------------------------------------------- #
def test_detail_permissions_and_shape(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    invoice_id = _confirmed_invoice(session_factory, actors["alice"], total="100.00")
    client.post(
        f"{BASE}/{report['id']}/items",
        json={"invoice_id": invoice_id, "amount": "20.00"},
        headers=_auth(actors["alice"]),
    )

    ok = client.get(f"{BASE}/{report['id']}", headers=_auth(actors["alice"]))
    assert ok.status_code == 200, ok.text
    data = ok.json()["data"]
    assert data["report"]["total_amount"] == "20.00"
    assert len(data["report"]["items"]) == 1
    assert data["signature"] is None and data["envelope"] is None
    assert data["verify_status"] == "unverified"
    body = ok.text
    for forbidden in ("ciphertext_b64", "wrapped_key_b64", "ciphertext_storage_key"):
        assert forbidden not in body

    assert client.get(f"{BASE}/{report['id']}", headers=_auth(actors["bob"])).status_code == 404
    # reviewer 看不见草稿（行级隔离），admin 一律 404
    assert client.get(f"{BASE}/{report['id']}", headers=_auth(actors["rita"])).status_code == 404
    assert client.get(f"{BASE}/{report['id']}", headers=_auth(actors["ada"])).status_code == 404

    # 提交之后 reviewer 可读（S5-3 的前置条件）
    with session_factory() as db:
        db.execute(
            sa_text("update report_forms set status = 'submitted' where id = :id"),
            {"id": report["id"]},
        )
        db.commit()
    assert client.get(f"{BASE}/{report['id']}", headers=_auth(actors["rita"])).status_code == 200


def test_list_mine_only_returns_own_reports(client, session_factory, actors) -> None:
    mine = [_create_report(client, actors["alice"]) for _ in range(2)]
    _create_report(client, actors["bob"])

    response = client.get(f"{BASE}/mine", headers=_auth(actors["alice"]))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"] == 2
    assert {item["report_no"] for item in data["items"]} == {r["report_no"] for r in mine}
    # 列表不含明细
    assert all(item["items"] == [] for item in data["items"])


def test_list_mine_filters_and_sorting(client, session_factory, actors) -> None:
    submitted = _create_report(client, actors["alice"], title="已提交")
    draft = _create_report(client, actors["alice"], title="草稿")
    yesterday = datetime.now(timezone.utc) - timedelta(days=2)
    with session_factory() as db:
        db.execute(
            sa_text(
                "update report_forms set status = 'submitted', submitted_at = :ts"
                " where id = :id"
            ),
            {"ts": yesterday, "id": submitted["id"]},
        )
        db.commit()

    by_status = client.get(
        f"{BASE}/mine", params={"status": "draft"}, headers=_auth(actors["alice"])
    ).json()["data"]
    assert [item["id"] for item in by_status["items"]] == [draft["id"]]

    # submitted_at DESC：空值（草稿）必须排最后，不能盖住已提交单据
    by_submitted = client.get(
        f"{BASE}/mine", params={"sort_by": "submitted_at"}, headers=_auth(actors["alice"])
    ).json()["data"]
    assert by_submitted["items"][0]["id"] == submitted["id"]
    assert by_submitted["items"][-1]["id"] == draft["id"]

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    in_range = client.get(
        f"{BASE}/mine",
        params={"date_from": today.isoformat(), "date_to": today.isoformat()},
        headers=_auth(actors["alice"]),
    ).json()["data"]
    # 两张单都是今天创建（submitted 只改了 submitted_at）
    assert in_range["total"] == 2

    future = (today + timedelta(days=1)).isoformat()
    empty = client.get(
        f"{BASE}/mine",
        params={"date_from": future, "date_to": future},
        headers=_auth(actors["alice"]),
    ).json()["data"]
    assert empty["total"] == 0

    bad = client.get(
        f"{BASE}/mine", params={"sort_by": "total_amount"}, headers=_auth(actors["alice"])
    )
    assert bad.status_code == 422 and bad.json()["code"] == 42200


def test_list_mine_roles(client, actors) -> None:
    for user in ("rita", "ada"):
        response = client.get(f"{BASE}/mine", headers=_auth(actors[user]))
        assert response.status_code == 403, (user, response.text)
        assert response.json()["code"] == 40301


# --------------------------------------------------------------------------- #
# 状态机（S5-2 会调用；本卡先把入口与允许表落地并单测）
# --------------------------------------------------------------------------- #
def test_transition_follows_state_machine(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    with session_factory() as db:
        user = db.get(User, actors["alice"].id)
        form = db.get(ReportForm, report["id"])
        chain = ["pending_sign", "signed", "encrypted", "submitted", "approved", "archived"]
        for target in chain:
            if target == "archived":
                # S5-6 的库级 CHECK：archived 必须有归档号/归档时间（生产由 archive_service 写）
                form.archive_no = "AR-TEST-0001"
                form.archive_at = datetime.now(timezone.utc)
            report_service.transition(db, form, target, user)
            assert form.status == target
        db.commit()
        assert form.version == len(chain)

        before = form.version
        report_service.transition(db, form, "archived", user)   # 幂等
        assert form.version == before
        db.rollback()


def test_transition_rejects_illegal_moves(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    with session_factory() as db:
        user = db.get(User, actors["alice"].id)
        form = db.get(ReportForm, report["id"])

        with pytest.raises(AppError):
            report_service.transition(db, form, "submitted", user)   # draft → submitted 跳过
        with pytest.raises(AppError):
            report_service.transition(db, form, "not_a_status", user)
        with pytest.raises(AppError):
            report_service.transition(db, form, "pending_sign", user, expected_version=99)
        db.rollback()


def test_transition_allows_rejected_to_pending_sign(client, session_factory, actors) -> None:
    report = _create_report(client, actors["alice"])
    with session_factory() as db:
        db.execute(
            sa_text("update report_forms set status = 'rejected' where id = :id"),
            {"id": report["id"]},
        )
        db.commit()
        user = db.get(User, actors["alice"].id)
        form = db.get(ReportForm, report["id"])
        report_service.transition(db, form, "pending_sign", user)
        assert form.status == ReportStatus.PENDING_SIGN.value
        db.commit()