"""S5-4 验签与解密：七步校验 / 篡改检出 / 凭据与 TTL / 临时预览 / 清理任务（集成）。

装置复用 `test_submit.py`（Docker 生成的 SM 身份、FakeStorage、真签名+提交链路）；
Redis 用 `InMemoryCredentialStore` 注入，不依赖真实 Redis。
"""
from __future__ import annotations

import base64
import hashlib
import io
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.report_items import ReportItem
from app.models.signatures import Signature
from app.models.users import User
from app.services.audit import verify_chain
from app.services.credential_store import (
    InMemoryCredentialStore,
    preview_key,
    verify_key,
)
from app.services.preview_service import cleanup_expired_previews
from app.services.verify_service import STEP_ORDER
from tests.db_helpers import alembic_config, temporary_database
from tests.test_submit import (
    FakeStorage,
    _auth,
    _client_for,
    _register_identities,
    _report_with_item,
    _sign,
    _submit,
    sm_identities,  # noqa: F401 - fixture
    rsa_identities,  # noqa: F401 - fixture
)

BASE = "/api/v1/finance"
pytestmark = pytest.mark.integration
FINANCE_PASSPHRASE = "test-pass-1234"


@pytest.fixture(scope="module")
def verify_database() -> Iterator[URL]:
    with temporary_database("study_ocr_verify_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(verify_database: URL):
    eng = create_engine(
        verify_database.render_as_string(hide_password=False), poolclass=NullPool
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
def credential_store() -> InMemoryCredentialStore:
    return InMemoryCredentialStore()


@pytest.fixture()
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture()
def client(session_factory, storage, credential_store) -> Iterator[TestClient]:
    with _client_for(
        session_factory,
        storage,
        settings_updates={"finance_key_passphrase": FINANCE_PASSPHRASE},
        credential_store=credential_store,
    ) as test_client:
        yield test_client


@pytest.fixture()
def submitted(
    client, session_factory, storage, actors, sm_identities
) -> int:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_id, _ = _report_with_item(client, actors, session_factory, storage)
    assert _sign(client, actors["alice"], report_id).status_code == 200
    assert _submit(client, actors["alice"], report_id, "idem-verify-0001").status_code == 200
    return report_id


def _verify(client, actors, report_id: int):
    return client.post(f"{BASE}/reports/{report_id}/verify", headers=_auth(actors["rita"]))


def _decrypt(client, actors, report_id: int, token: str):
    return client.post(
        f"{BASE}/reports/{report_id}/decrypt",
        json={"verify_token": token},
        headers=_auth(actors["rita"]),
    )


# --------------------------------------------------------------------------- #
# 验签：七步与成功路径
# --------------------------------------------------------------------------- #
def test_verify_success(
    client, session_factory, actors, credential_store, submitted, sm_identities
) -> None:
    response = _verify(client, actors, submitted)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["verified"] is True
    assert [step["step"] for step in data["steps"]] == list(STEP_ORDER)
    assert all(step["ok"] is True for step in data["steps"])
    assert data["cert_fp"] == sm_identities["alice"]["fingerprint"]
    assert data["payload_sha256_match"] is True
    assert data["verify_token"]
    assert data["verify_token_expires_in"] == 1800
    assert credential_store.get(verify_key(submitted)) == data["verify_token"]

    with session_factory() as db:
        audits = db.execute(
            sa_text("select action || ':' || coalesce(detail_json->>'result','') "
                    "from audit_logs where action = 'verify'")
        ).scalars().all()
        assert "verify:ok" in audits
        assert verify_chain(db).ok is True


def test_verify_detects_tampered_business_rows(
    client, session_factory, actors, submitted
) -> None:
    """签名域没改、业务行被改：必须靠"从库内重算"检出（而不是比存下的摘要）。"""
    with session_factory() as db:
        db.execute(
            sa_text("update report_forms set title = '偷偷改过的标题' where id = :id"),
            {"id": submitted},
        )
        db.commit()

    response = _verify(client, actors, submitted)

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["data"]["reason_code"] == "digest_mismatch"
    assert body["data"]["failed_step"] == "digest_recheck"
    assert "偷偷改过的标题" not in response.text
    with session_factory() as db:
        signature = db.scalars(
            select(Signature).where(Signature.form_id == submitted, Signature.status == "active")
        ).one()
        assert (signature.verified, signature.verify_result) == (False, "digest_mismatch")
        assert verify_chain(db).ok is True

    listing = client.get(
        f"{BASE}/pending", params={"verify_status": "failed"}, headers=_auth(actors["rita"])
    ).json()["data"]
    assert [item["report_id"] for item in listing["items"]] == [submitted]


def test_verify_detects_tampered_canonical(
    client, session_factory, actors, submitted
) -> None:
    with session_factory() as db:
        db.execute(
            sa_text(
                "update report_forms set canonical_json = canonical_json || ' ' where id = :id"
            ),
            {"id": submitted},
        )
        db.commit()

    response = _verify(client, actors, submitted)

    assert response.status_code == 409
    assert response.json()["data"] == {
        "reason_code": "bad_signature",
        "failed_step": "signature",
    }


def test_verify_mac_failure_does_not_write_verify_result(
    client, session_factory, actors, submitted
) -> None:
    with session_factory() as db:
        envelope = db.execute(
            sa_text("select id, ciphertext_b64 from envelopes where form_id = :id"),
            {"id": submitted},
        ).one()
        raw = bytearray(base64.b64decode(envelope.ciphertext_b64))
        raw[0] ^= 0x01
        db.execute(
            sa_text("update envelopes set ciphertext_b64 = :data where id = :id"),
            {"data": base64.b64encode(bytes(raw)).decode("ascii"), "id": envelope.id},
        )
        db.commit()

    response = _verify(client, actors, submitted)

    assert response.status_code == 409
    assert response.json()["data"]["reason_code"] == "mac_mismatch"
    assert response.json()["data"]["failed_step"] == "mac"
    with session_factory() as db:
        signature = db.scalars(
            select(Signature).where(Signature.form_id == submitted, Signature.status == "active")
        ).one()
        # SCHEMA §9：MAC/填充失败只进审计与响应，不改写签名结论
        assert (signature.verified, signature.verify_result) == (True, "ok")


def test_verify_wrong_finance_passphrase(
    session_factory, storage, actors, credential_store, submitted
) -> None:
    with _client_for(
        session_factory,
        storage,
        settings_updates={"finance_key_passphrase": "definitely-wrong"},
        credential_store=credential_store,
    ) as wrong_client:
        response = _verify(wrong_client, actors, submitted)

    assert response.status_code == 422
    assert response.json()["code"] == 42200
    assert response.json()["message"] == "财务私钥口令未配置或不可用"


def test_verify_revoked_certificate(
    client, session_factory, actors, submitted
) -> None:
    with session_factory() as db:
        db.execute(
            sa_text(
                "update certificates set revoked = true, revoked_at = now()"
                " where owner_type = 'user'"
            )
        )
        db.commit()

    response = _verify(client, actors, submitted)

    assert response.status_code == 409
    assert response.json()["data"]["reason_code"] == "cert_invalid"
    assert response.json()["data"]["failed_step"] == "cert_check"
    with session_factory() as db:
        signature = db.scalars(
            select(Signature).where(Signature.form_id == submitted, Signature.status == "active")
        ).one()
        assert signature.verify_result == "cert_invalid"


def test_verify_missing_envelope(client, session_factory, actors, submitted) -> None:
    with session_factory() as db:
        db.execute(sa_text("delete from envelopes where form_id = :id"), {"id": submitted})
        db.commit()

    response = _verify(client, actors, submitted)

    assert response.status_code == 409
    assert response.json()["data"]["reason_code"] == "bad_envelope"
    assert response.json()["data"]["failed_step"] == "envelope_lookup"


def test_verify_permissions(client, actors, submitted) -> None:
    assert client.post(f"{BASE}/reports/{submitted}/verify").status_code == 401
    for role in ("alice", "ada"):
        response = client.post(f"{BASE}/reports/{submitted}/verify", headers=_auth(actors[role]))
        assert response.status_code == 403, (role, response.text)
    assert client.post(f"{BASE}/reports/999999/verify", headers=_auth(actors["rita"])).status_code == 404


# --------------------------------------------------------------------------- #
# 解密与临时预览
# --------------------------------------------------------------------------- #
def test_decrypt_and_preview_roundtrip(
    client, session_factory, actors, submitted, credential_store, storage
) -> None:
    verify_data = _verify(client, actors, submitted).json()["data"]

    response = _decrypt(client, actors, submitted, verify_data["verify_token"])

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    token = data["preview_token"]
    with session_factory() as db:
        report_no = db.get(ReportForm, submitted).report_no
        invoice_id = db.scalar(
            select(ReportItem.invoice_id).where(ReportItem.form_id == submitted)
        )
        file_row = db.scalars(
            select(InvoiceFile)
            .join(Invoice, Invoice.file_id == InvoiceFile.id)
            .where(Invoice.id == invoice_id)
        ).one()
    assert data["fields_preview"]["report_no"] == report_no
    assert data["size"] > 0 and data["ttl_seconds"] == 1800

    prefix = f"previews/{token}"
    assert f"{prefix}/report.json" in storage.objects
    assert any(key.startswith(f"{prefix}/files/") for key in storage.objects)

    # 预览 JSON 可重复读
    first = client.get(f"{BASE}/preview/{token}", headers=_auth(actors["rita"]))
    second = client.get(f"{BASE}/preview/{token}", headers=_auth(actors["rita"]))
    assert first.status_code == second.status_code == 200
    assert first.json()["report_no"] == report_no

    # 影像明文与 invoice_files.sha256 一致
    image = client.get(
        f"{BASE}/preview/{token}/files/{file_row.id}", headers=_auth(actors["rita"])
    )
    assert image.status_code == 200
    assert hashlib.sha256(image.content).hexdigest() == file_row.sha256


def test_decrypt_requires_valid_verify_token(
    client, actors, submitted
) -> None:
    missing = _decrypt(client, actors, submitted, "not-a-real-token-123")
    assert missing.status_code == 409
    assert missing.json()["data"]["reason_code"] == "verify_token_invalid"


def test_preview_expired_returns_410(
    client, actors, submitted, credential_store
) -> None:
    verify_data = _verify(client, actors, submitted).json()["data"]
    token = _decrypt(client, actors, submitted, verify_data["verify_token"]).json()["data"][
        "preview_token"
    ]
    credential_store.delete(preview_key(token))

    report = client.get(f"{BASE}/preview/{token}", headers=_auth(actors["rita"]))
    assert report.status_code == 410 and report.json()["code"] == 41001


def test_cleanup_expired_previews_deletes_only_old(
    storage, actors
) -> None:
    now = datetime.now(timezone.utc)
    storage.put("previews/old/a.bin", io.BytesIO(b"old"), 3, "application/octet-stream")
    storage.put("previews/new/b.bin", io.BytesIO(b"new"), 3, "application/octet-stream")
    storage.modified["previews/old/a.bin"] = now - timedelta(seconds=3600)
    storage.modified["previews/new/b.bin"] = now

    settings = get_settings().model_copy(update={"preview_token_ttl_seconds": 1800})
    deleted = cleanup_expired_previews(storage, settings, now=now)

    assert deleted == 1
    assert "previews/old/a.bin" not in storage.objects
    assert "previews/new/b.bin" in storage.objects


# --------------------------------------------------------------------------- #
# RSA 对照 provider
# --------------------------------------------------------------------------- #
def test_rsa_provider_verify_and_decrypt(
    session_factory, storage, actors, credential_store, rsa_identities
) -> None:
    with _client_for(
        session_factory,
        storage,
        algo="rsa",
        settings_updates={"finance_key_passphrase": FINANCE_PASSPHRASE},
        credential_store=credential_store,
    ) as client:
        _register_identities(session_factory, actors["alice"], rsa_identities, algo="RSA")
        report_id, _ = _report_with_item(client, actors, session_factory, storage)
        assert _sign(client, actors["alice"], report_id).status_code == 200
        assert _submit(client, actors["alice"], report_id, "idem-rsa-verify-1").status_code == 200

        verified = _verify(client, actors, report_id)
        assert verified.status_code == 200, verified.text
        assert all(step["ok"] for step in verified.json()["data"]["steps"])

        decrypted = _decrypt(
            client, actors, report_id, verified.json()["data"]["verify_token"]
        )
        assert decrypted.status_code == 200, decrypted.text
        token = decrypted.json()["data"]["preview_token"]
        assert client.get(f"{BASE}/preview/{token}", headers=_auth(actors["rita"])).status_code == 200