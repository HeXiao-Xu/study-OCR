"""S5-6 归档与检索：归档号/不可逆/只读/检索/口令加密归档包（集成）。"""
from __future__ import annotations

import io
import json
import os
import secrets
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.crypto.archive_crypto import open_archive
from app.models.report_forms import ReportForm
from app.models.users import User
from app.services import archive_service
from app.services.audit import verify_chain
from app.utils.time import BUSINESS_TZ
from tests.db_helpers import alembic_config, temporary_database
from tests.test_submit import (
    _auth,
    _client_for,
    _register_identities,
    _report_with_item,
    _sign,
    _submit,
    sm_identities,  # noqa: F401 - fixture
)

BASE = "/api/v1"
FINANCE_PASSPHRASE = "test-pass-1234"
EXPORT_PASS = "export-pass-1234"
ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def archive_database() -> Iterator[URL]:
    with temporary_database("study_ocr_archive_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(archive_database: URL):
    eng = create_engine(
        archive_database.render_as_string(hide_password=False), poolclass=NullPool
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
            "bob": User(username="bob", real_name="B", role="submitter", status="active"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


@pytest.fixture()
def credential_store():
    from app.services.credential_store import InMemoryCredentialStore

    return InMemoryCredentialStore()


@pytest.fixture()
def storage():
    from tests.test_submit import FakeStorage

    return FakeStorage()


@pytest.fixture(autouse=True)
def registered_identities(session_factory, actors, sm_identities) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")


@pytest.fixture()
def client(session_factory, storage, credential_store) -> Iterator[TestClient]:
    with _client_for(
        session_factory,
        storage,
        settings_updates={"finance_key_passphrase": FINANCE_PASSPHRASE},
        credential_store=credential_store,
    ) as test_client:
        yield test_client


def _version(session_factory, report_id: int) -> int:
    with session_factory() as db:
        return db.scalar(select(ReportForm.version).where(ReportForm.id == report_id))


def _approve(client, session_factory, actors, report_id: int) -> None:
    verified = client.post(
        f"{BASE}/finance/reports/{report_id}/verify", headers=_auth(actors["rita"])
    )
    assert verified.status_code == 200, verified.text
    approved = client.post(
        f"{BASE}/finance/reports/{report_id}/review",
        json={
            "decision": "approve",
            "version": _version(session_factory, report_id),
            "verify_token": verified.json()["data"]["verify_token"],
        },
        headers=_auth(actors["rita"]),
    )
    assert approved.status_code == 200, approved.text


def _submit_one(client, session_factory, storage, actors) -> int:
    report_id, _ = _report_with_item(client, actors, session_factory, storage)
    assert _sign(client, actors["alice"], report_id).status_code == 200
    assert _submit(
        client, actors["alice"], report_id, f"idem-archive-{secrets.token_hex(4)}"
    ).status_code == 200
    return report_id


def _approved(client, session_factory, storage, actors, **kwargs) -> int:
    report_id = _submit_one(client, session_factory, storage, actors)
    _approve(client, session_factory, actors, report_id)
    return report_id


def _archive(client, actors, report_id: int, *, confirm: bool = True, role: str = "ada"):
    return client.post(
        f"{BASE}/admin/reports/{report_id}/archive",
        json={"confirm": confirm},
        headers=_auth(actors[role]),
    )


# --------------------------------------------------------------------------- #
# 归档
# --------------------------------------------------------------------------- #
def test_archive_success_generates_number_and_audit(
    client, session_factory, storage, actors
) -> None:
    report_id = _approved(client, session_factory, storage, actors)

    response = _archive(client, actors, report_id)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "archived"
    assert data["archive_no"].startswith("AR-")
    assert data["archive_no"].split("-")[-1].isdigit()
    assert data["archive_at"]

    with session_factory() as db:
        form = db.get(ReportForm, report_id)
        assert form.status == "archived"
        assert form.archive_no == data["archive_no"]
        assert form.archive_at is not None
        assert verify_chain(db).ok is True
        archive_audits = db.execute(
            sa_text("select detail_json->>'archive_no' from audit_logs where action = 'archive'")
        ).scalars().all()
        assert data["archive_no"] in archive_audits

    # 归档后详情仍可看（本人 / reviewer），带归档号
    for role in ("alice", "rita"):
        detail = client.get(f"{BASE}/reports/{report_id}", headers=_auth(actors[role]))
        assert detail.status_code == 200
        assert detail.json()["data"]["report"]["archive_no"] == data["archive_no"]


def test_archive_requires_confirm(client, session_factory, storage, actors) -> None:
    report_id = _approved(client, session_factory, storage, actors)

    response = _archive(client, actors, report_id, confirm=False)

    assert response.status_code == 422
    assert response.json()["code"] == 42202
    with session_factory() as db:
        assert db.get(ReportForm, report_id).status == "approved"


def test_archive_rejects_wrong_status_and_repeat(
    client, session_factory, storage, actors
) -> None:
    submitted = _submit_one(client, session_factory, storage, actors)
    assert _archive(client, actors, submitted).status_code == 409

    approved = _approved(client, session_factory, storage, actors)
    assert _archive(client, actors, approved).status_code == 200
    again = _archive(client, actors, approved)
    assert again.status_code == 409
    assert again.json()["data"]["reason_code"] == "already_archived"


def test_archive_permissions(client, session_factory, storage, actors) -> None:
    report_id = _approved(client, session_factory, storage, actors)
    assert (
        client.post(
            f"{BASE}/admin/reports/{report_id}/archive", json={"confirm": True}
        ).status_code
        == 401
    )
    for role in ("alice", "rita"):
        assert _archive(client, actors, report_id, role=role).status_code == 403
    assert (
        client.post(
            f"{BASE}/admin/reports/999999/archive",
            json={"confirm": True},
            headers=_auth(actors["ada"]),
        ).status_code
        == 404
    )


def test_concurrent_archive_gets_unique_numbers(
    client, session_factory, storage, actors
) -> None:
    first, second = _submit_one(client, session_factory, storage, actors), _submit_one(
        client, session_factory, storage, actors
    )
    for report_id in (first, second):
        _approve(client, session_factory, actors, report_id)

    with session_factory() as db:
        admin = db.get(User, actors["ada"].id)

    def _run(report_id: int) -> str:
        db = session_factory()
        try:
            return archive_service.archive_report(
                db, admin, report_id, confirm=True
            ).archive_no
        finally:
            db.close()

    numbers = [_run(first), _run(second)]
    assert len(set(numbers)) == 2
    with session_factory() as db:
        assert verify_chain(db).ok is True


def test_archived_report_is_read_only(
    client, session_factory, storage, actors
) -> None:
    report_id = _approved(client, session_factory, storage, actors)
    assert _archive(client, actors, report_id).status_code == 200
    version = _version(session_factory, report_id)

    add_item = client.post(
        f"{BASE}/reports/{report_id}/items",
        json={"invoice_id": 999999, "amount": "1.00"},
        headers=_auth(actors["alice"]),
    )
    sign = _sign(client, actors["alice"], report_id)
    submit = _submit(client, actors["alice"], report_id, "idem-after-archive")
    review = client.post(
        f"{BASE}/finance/reports/{report_id}/review",
        json={"decision": "reject", "comment": "不应允许", "version": version},
        headers=_auth(actors["rita"]),
    )

    for response in (add_item, sign, submit, review):
        assert response.status_code == 409, response.text


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #
def test_search_archived_only_and_row_isolation(
    client, session_factory, storage, actors
) -> None:
    first = _approved(client, session_factory, storage, actors)
    second = _approved(client, session_factory, storage, actors)
    approved_only = _approved(client, session_factory, storage, actors)
    for report_id in (first, second):
        assert _archive(client, actors, report_id).status_code == 200

    as_alice = client.get(f"{BASE}/reports", headers=_auth(actors["alice"]))
    assert as_alice.status_code == 200, as_alice.text
    data = as_alice.json()["data"]
    assert data["total"] == 2
    assert {item["id"] for item in data["items"]} == {first, second}
    assert all(item["status"] == "archived" for item in data["items"])
    assert all(item["archive_no"] for item in data["items"])
    assert approved_only not in {item["id"] for item in data["items"]}

    as_reviewer = client.get(f"{BASE}/reports", headers=_auth(actors["rita"]))
    assert as_reviewer.status_code == 200
    assert {item["id"] for item in as_reviewer.json()["data"]["items"]} == {first, second}

    assert client.get(f"{BASE}/reports", headers=_auth(actors["ada"])).status_code == 403
    assert client.get(f"{BASE}/reports").status_code == 401
    assert (
        client.get(f"{BASE}/reports", params={"status": "draft"}, headers=_auth(actors["rita"]))
        .status_code
        == 422
    )


def test_search_filters(client, session_factory, storage, actors) -> None:
    small = _approved(client, session_factory, storage, actors)
    large = _approved(client, session_factory, storage, actors)
    assert _archive(client, actors, small).status_code == 200
    assert _archive(client, actors, large).status_code == 200
    with session_factory() as db:
        report_no = db.get(ReportForm, small).report_no

    by_q = client.get(
        f"{BASE}/reports", params={"q": report_no}, headers=_auth(actors["rita"])
    )
    assert [item["id"] for item in by_q.json()["data"]["items"]] == [small]

    by_amount = client.get(
        f"{BASE}/reports",
        params={"amount_min": "50.00", "amount_max": "150.00"},
        headers=_auth(actors["rita"]),
    )
    assert by_amount.json()["data"]["total"] == 2

    today = datetime.now(BUSINESS_TZ).date().isoformat()
    by_date = client.get(
        f"{BASE}/reports",
        params={"date_from": today, "date_to": today},
        headers=_auth(actors["rita"]),
    )
    assert by_date.json()["data"]["total"] == 2


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #
def test_export_archive_bundle_contents(
    client, session_factory, storage, actors
) -> None:
    report_id = _approved(client, session_factory, storage, actors)
    archived = _archive(client, actors, report_id).json()["data"]

    response = client.post(
        f"{BASE}/reports/{report_id}/export",
        json={"passphrase": EXPORT_PASS},
        headers=_auth(actors["rita"]),
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"
    assert "archive.zip.enc" in response.headers["content-disposition"]

    zip_bytes = open_archive(response.content, EXPORT_PASS)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert names == {
            "archive.json",
            "manifest.csv",
            "canonical.json",
            "signatures/signature.json",
        }
        assert all(not name.startswith("files/") for name in names)
        manifest = zf.read("manifest.csv")
        assert manifest.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in manifest
        doc = json.loads(zf.read("archive.json"))
        assert doc["report"]["report_no"] == archived["report_no"]
        assert doc["report"]["archive_no"] == archived["archive_no"]
        assert doc["items"] and doc["items"][0]["invoice_id"]
        signature_doc = json.loads(zf.read("signatures/signature.json"))
        assert signature_doc["signature"]["verify_result"] == "ok"
        assert "BEGIN CERTIFICATE" in signature_doc["certificate_pem"]
        with session_factory() as db:
            canonical = db.get(ReportForm, report_id).canonical_json
        assert zf.read("canonical.json").decode("utf-8") == canonical
        # 不把影像/密文塞进归档包（`ciphertext_len` 只是长度元数据，允许）
        blob = b"".join(zf.read(name) for name in names)
        assert b"JFIF" not in blob
        for forbidden in (b"ciphertext_b64", b"wrapped_key_b64", b"ciphertext_storage_key", b"storage_key"):
            assert forbidden not in blob


def test_export_permissions_and_preconditions(
    client, session_factory, storage, actors
) -> None:
    archived = _approved(client, session_factory, storage, actors)
    assert _archive(client, actors, archived).status_code == 200
    submitted = _submit_one(client, session_factory, storage, actors)

    # 非本人 submitter → 404；admin → 403；未归档 → 409；短口令 → 422
    assert (
        client.post(
            f"{BASE}/reports/{archived}/export",
            json={"passphrase": EXPORT_PASS},
            headers=_auth(actors["bob"]),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{BASE}/reports/{archived}/export",
            json={"passphrase": EXPORT_PASS},
            headers=_auth(actors["ada"]),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{BASE}/reports/{submitted}/export",
            json={"passphrase": EXPORT_PASS},
            headers=_auth(actors["alice"]),
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"{BASE}/reports/{archived}/export",
            json={"passphrase": "short"},
            headers=_auth(actors["alice"]),
        ).status_code
        == 422
    )


def test_open_archive_script_roundtrip(
    client, session_factory, storage, actors, tmp_path
) -> None:
    report_id = _approved(client, session_factory, storage, actors)
    assert _archive(client, actors, report_id).status_code == 200
    bundle = client.post(
        f"{BASE}/reports/{report_id}/export",
        json={"passphrase": EXPORT_PASS},
        headers=_auth(actors["alice"]),
    )
    archive_file = tmp_path / "bundle.zip.enc"
    archive_file.write_bytes(bundle.content)
    out_zip = tmp_path / "out.zip"
    extract_dir = tmp_path / "extracted"

    result = subprocess.run(
        [
            PYTHON,
            str(ROOT / "scripts" / "open_archive.py"),
            str(archive_file),
            "--out",
            str(out_zip),
            "--extract",
            str(extract_dir),
        ],
        env={**os.environ, "STUDYOCR_ARCHIVE_PASSPHRASE": EXPORT_PASS},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert out_zip.exists()
    assert (extract_dir / "archive.json").exists()
    assert (extract_dir / "manifest.csv").exists()
    with zipfile.ZipFile(out_zip) as zf:
        assert len(zf.namelist()) == 4