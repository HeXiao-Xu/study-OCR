"""S5-3 财务待审列表：列表/筛选/排序/权限/详情/CSV/无 N+1（集成，Docker PostgreSQL）。

装置复用 `test_submit.py`（SM 身份由 Docker 现场生成、FakeStorage、真签名+提交链路），
避免两套夹具漂移；本文件只补财务视角的断言。
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.models.signatures import Signature
from app.models.users import User
from app.schemas.finance import FinancePendingQuery
from app.services import finance_service
from tests.db_helpers import alembic_config, temporary_database
from tests.test_submit import (
    FakeStorage,
    _auth,
    _client_for,
    _register_identities,
    _report_with_item,
    _sign,
    _submit,
    sm_identities,  # noqa: F401 - 作为 fixture 被本模块使用
)

BASE = "/api/v1/finance"
TOTALS = ("100.00", "200.00", "300.00", "400.00", "500.00")
pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 装置
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def finance_database() -> Iterator[URL]:
    with temporary_database("study_ocr_finance_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(finance_database: URL):
    eng = create_engine(
        finance_database.render_as_string(hide_password=False), poolclass=NullPool
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
        conn.execute(
            sa_text("TRUNCATE users, certificates, key_records RESTART IDENTITY CASCADE")
        )
    yield


@pytest.fixture()
def actors(session_factory, clean_db) -> dict[str, User]:
    with session_factory() as db:
        users = {
            "alice": User(username="alice", real_name="A", role="submitter",
                          status="active", dept="计算机学院"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


@pytest.fixture()
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture()
def client(session_factory, storage) -> Iterator[TestClient]:
    with _client_for(session_factory, storage) as test_client:
        yield test_client


@pytest.fixture()
def five_reports(
    client, session_factory, storage, actors, sm_identities
) -> list[int]:
    """5 张已提交的单（金额 100~500），列表/筛选/排序/CSV 的公共底料。"""
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_ids: list[int] = []
    for index, total in enumerate(TOTALS):
        report_id, _ = _report_with_item(
            client, actors, session_factory, storage, total=total
        )
        signed = _sign(client, actors["alice"], report_id)
        assert signed.status_code == 200, signed.text
        submitted = _submit(
            client, actors["alice"], report_id, f"idem-fin-{index:04d}"
        )
        assert submitted.status_code == 200, submitted.text
        report_ids.append(report_id)
    return report_ids


def _pending(client, user, **params) -> dict:
    response = client.get(f"{BASE}/pending", params=params, headers=_auth(user))
    assert response.status_code == 200, response.text
    return response.json()["data"]


# --------------------------------------------------------------------------- #
# 列表
# --------------------------------------------------------------------------- #
def test_pending_lists_five_submitted_reports(
    client, session_factory, actors, five_reports
) -> None:
    data = _pending(client, actors["rita"])

    assert data["total"] == 5 and len(data["items"]) == 5
    assert {item["report_id"] for item in data["items"]} == set(five_reports)
    # 默认按提交时间倒序（最后提交的在最前）
    assert data["items"][0]["report_id"] == five_reports[-1]
    first = next(item for item in data["items"] if item["report_id"] == five_reports[0])
    assert first["report_no"].startswith("RPT-")
    assert first["applicant_id"] == actors["alice"].id
    assert first["applicant_name"] == "A"
    assert first["total_amount"] == "100.00"
    assert first["item_count"] == 1
    assert first["submitted_at"]
    # S5-2 修正后：提交时系统自动验签落库，"待审"默认显示通过
    assert {item["verify_status"] for item in data["items"]} == {"ok"}

    with session_factory() as db:
        signatures = db.scalars(
            select(Signature).where(
                Signature.form_id.in_(five_reports),
                Signature.status == "active",
            )
        ).all()
        assert len(signatures) == 5
        assert all(sig.verified and sig.verify_result == "ok" for sig in signatures)
        assert all(sig.verified_at is not None for sig in signatures)
        assert all(sig.verifier_id is None for sig in signatures)  # 系统自动验签

    response = client.get(f"{BASE}/pending", headers=_auth(actors["rita"]))
    for forbidden in ("ciphertext_b64", "wrapped_key_b64", "ciphertext_storage_key", "storage_key"):
        assert forbidden not in response.text


def test_pending_verify_status_filter(
    client, session_factory, actors, five_reports
) -> None:
    with session_factory() as db:
        db.execute(
            sa_text(
                "update signatures set verified = false, verify_result = null"
                " where form_id = :form and status = 'active'"
            ),
            {"form": five_reports[0]},
        )
        db.execute(
            sa_text(
                "update signatures set verified = false, verify_result = 'bad_signature'"
                " where form_id = :form and status = 'active'"
            ),
            {"form": five_reports[1]},
        )
        db.commit()

    unverified = _pending(client, actors["rita"], verify_status="unverified")
    failed = _pending(client, actors["rita"], verify_status="failed")
    ok = _pending(client, actors["rita"], verify_status="ok")

    assert [item["report_id"] for item in unverified["items"]] == [five_reports[0]]
    assert [item["report_id"] for item in failed["items"]] == [five_reports[1]]
    assert {item["report_id"] for item in ok["items"]} == set(five_reports[2:])
    assert unverified["items"][0]["verify_status"] == "unverified"
    assert failed["items"][0]["verify_status"] == "failed"


def test_pending_q_and_date_filters(client, session_factory, actors, five_reports) -> None:
    with session_factory() as db:
        report_no = db.scalar(
            sa_text("select report_no from report_forms where id = :id"),
            {"id": five_reports[0]},
        )

    assert _pending(client, actors["rita"], q=report_no)["total"] == 1
    assert _pending(client, actors["rita"], q="alice")["total"] == 5
    assert _pending(client, actors["rita"], q="不存在的单号")["total"] == 0

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    tomorrow = today + timedelta(days=1)
    assert _pending(
        client, actors["rita"],
        submitted_from=today.isoformat(), submitted_to=today.isoformat(),
    )["total"] == 5
    assert _pending(
        client, actors["rita"],
        submitted_from=tomorrow.isoformat(), submitted_to=tomorrow.isoformat(),
    )["total"] == 0

    bad = client.get(
        f"{BASE}/pending",
        params={"submitted_from": tomorrow.isoformat(), "submitted_to": today.isoformat()},
        headers=_auth(actors["rita"]),
    )
    assert bad.status_code == 422 and bad.json()["code"] == 42200


def test_pending_sorting_whitelist(client, actors, five_reports) -> None:
    asc = _pending(client, actors["rita"], sort_by="total_amount", order="asc")
    desc = _pending(client, actors["rita"], sort_by="total_amount", order="desc")

    assert [item["total_amount"] for item in asc["items"]] == list(TOTALS)
    assert [item["total_amount"] for item in desc["items"]] == list(reversed(TOTALS))

    bad = client.get(
        f"{BASE}/pending", params={"sort_by": "created_at"}, headers=_auth(actors["rita"])
    )
    assert bad.status_code == 422 and bad.json()["code"] == 42200


def test_pending_permissions(client, actors, five_reports) -> None:
    assert client.get(f"{BASE}/pending").status_code == 401
    for role in ("alice", "ada"):
        response = client.get(f"{BASE}/pending", headers=_auth(actors[role]))
        assert response.status_code == 403, (role, response.text)
        assert response.json()["code"] == 40301


def test_pending_uses_two_queries(
    session_factory, actors, sm_identities, client, storage
) -> None:
    """任务卡的坑：列表不做实时验签/逐行回查——整页只应有 count + 分页两条 SELECT。"""
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    for index, total in enumerate(TOTALS):
        report_id, _ = _report_with_item(
            client, actors, session_factory, storage, total=total
        )
        assert _sign(client, actors["alice"], report_id).status_code == 200
        assert _submit(
            client, actors["alice"], report_id, f"idem-count-{index:04d}"
        ).status_code == 200

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    with session_factory() as db:
        reviewer = db.get(User, actors["rita"].id)
        event.listen(db.get_bind(), "before_cursor_execute", _record)
        try:
            page = finance_service.list_pending(
                db, reviewer, FinancePendingQuery(page=1, page_size=20)
            )
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", _record)

    assert page.total == 5 and len(page.items) == 5
    assert len(statements) == 2, statements


# --------------------------------------------------------------------------- #
# 详情
# --------------------------------------------------------------------------- #
def test_detail_returns_metadata_without_secrets(
    client, session_factory, actors, five_reports
) -> None:
    response = client.get(f"{BASE}/{five_reports[0]}", headers=_auth(actors["rita"]))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["report"]["report_no"].startswith("RPT-")
    assert data["report"]["total_amount"] == "100.00"
    assert data["applicant_name"] == "A"
    assert data["verify_status"] == "ok"
    assert data["signature"]["algo"] == "SM2"
    assert data["signature"]["verify_result"] == "ok"
    assert len(data["envelope"]["payload_sha256"]) == 64
    assert data["envelope"]["recipient_cert_fp"]

    assert len(data["attachments"]) == 1
    attachment = data["attachments"][0]
    assert attachment["filename"] == "a.jpg"
    assert attachment["size"] > 0
    assert len(attachment["sha256"]) == 64
    assert attachment["page_count"] == 1
    assert attachment["envelope"]["key_algo"] == "SM2"
    assert attachment["envelope"]["file_id"] == attachment["file_id"]

    for forbidden in ("ciphertext_b64", "wrapped_key_b64", "ciphertext_storage_key", "storage_key"):
        assert forbidden not in response.text


def test_detail_permissions_and_isolation(
    client, session_factory, actors, five_reports
) -> None:
    for role in ("alice", "ada"):
        response = client.get(f"{BASE}/{five_reports[0]}", headers=_auth(actors[role]))
        assert response.status_code == 403, (role, response.text)

    assert client.get(f"{BASE}/999999", headers=_auth(actors["rita"])).status_code == 404

    # 草稿（未提交）对 reviewer 不可见 → 404
    created = client.post(
        "/api/v1/reports",
        json={"title": "草稿", "reason": "未提交"},
        headers=_auth(actors["alice"]),
    )
    assert created.status_code == 201
    draft_id = created.json()["data"]["id"]
    assert client.get(f"{BASE}/{draft_id}", headers=_auth(actors["rita"])).status_code == 404


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def test_csv_export_bom_and_content(client, actors, five_reports) -> None:
    response = client.get(f"{BASE}/pending/export", headers=_auth(actors["rita"]))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "finance-pending-" in response.headers["content-disposition"]
    raw = response.content
    assert raw.startswith(b"\xef\xbb\xbf")       # UTF-8 BOM（Excel 中文不乱码）
    assert b"\r\n" in raw

    text = raw.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line]
    assert lines[0] == "单号,申请人,金额,票据数,提交时间,验签状态"
    assert len(lines) == 6
    assert "alice".lower() in lines[1].lower() or "A" in lines[1]
    assert "通过" in text


def test_csv_export_matches_filters(client, session_factory, actors, five_reports) -> None:
    with session_factory() as db:
        report_no = db.scalar(
            sa_text("select report_no from report_forms where id = :id"),
            {"id": five_reports[2]},
        )
    filtered = client.get(
        f"{BASE}/pending/export", params={"q": report_no}, headers=_auth(actors["rita"])
    )
    lines = [line for line in filtered.content.decode("utf-8-sig").splitlines() if line]
    assert len(lines) == 2 and report_no in lines[1]

    empty = client.get(
        f"{BASE}/pending/export",
        params={"verify_status": "failed"},
        headers=_auth(actors["rita"]),
    )
    empty_lines = [line for line in empty.content.decode("utf-8-sig").splitlines() if line]
    assert len(empty_lines) == 1  # 只有表头


def test_csv_export_permissions(client, actors, five_reports) -> None:
    for role in ("alice", "ada"):
        response = client.get(f"{BASE}/pending/export", headers=_auth(actors[role]))
        assert response.status_code == 403, (role, response.text)


def test_csv_export_limit(client, actors, five_reports, monkeypatch) -> None:
    monkeypatch.setattr(finance_service, "CSV_LIMIT", 2)
    response = client.get(f"{BASE}/pending/export", headers=_auth(actors["rita"]))
    assert response.status_code == 422 and response.json()["code"] == 42200