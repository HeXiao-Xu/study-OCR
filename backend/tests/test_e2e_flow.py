"""S5-7 端到端联调：上传 → OCR(确定性桩) → 确认 → 建单 → 签名/提交 → 验签/解密 → 审核 → 归档。

OCR 用确定性假引擎（真 Paddle 的覆盖在 S3 测试与基线脚本），其余全部是真的：
HTTP 路由、OCR 作业执行路径、字段落库、确认闸门、SM2/SM4 签名与信封、MinIO 语义（FakeStorage）、
Redis 凭据（InMemoryCredentialStore）、数据库事务与审计链。
"""
from __future__ import annotations

import base64
import hashlib
import io
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, func, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.certificates import Certificate
from app.models.envelopes import Envelope as EnvelopeRow
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.review_logs import ReviewLog
from app.models.users import User
from app.ocr.types import OcrResult
from app.services import job_service
from app.services.audit import verify_chain
from app.services.credential_store import InMemoryCredentialStore, verify_key
from app.services.key_service import load_finance_private_key, provider_for
from app.services.ocr_pipeline import PipelineDeps, run_pipeline
from app.services.report_crypto import open_sealed
from tests.db_helpers import alembic_config, temporary_database
from tests.test_ocr_pipeline import _FakeLockStore, _fake_pages, _line
from tests.test_submit import (
    PASSPHRASE,
    _auth,
    _client_for,
    _register_identities,
    sm_identities,  # noqa: F401 - fixture
)

BASE = "/api/v1"
FINANCE_PASSPHRASE = "test-pass-1234"
ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def e2e_database() -> Iterator[URL]:
    with temporary_database("study_ocr_e2e_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(e2e_database: URL):
    eng = create_engine(e2e_database.render_as_string(hide_password=False), poolclass=NullPool)
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


@pytest.fixture(autouse=True)
def registered(session_factory, actors, sm_identities) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")


class _FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[int] = []

    async def enqueue_ocr(self, job_id: int) -> None:
        self.jobs.append(job_id)


_STUB_LINES = [
    _line("电子发票（普通发票） 全电发票 数电票", 0, y=10),
    _line("发票号码：12345678901234567890", 1, y=120),
    _line("开票日期：2026-03-01", 2, y=160),
    _line("合计  ¥100.00  ¥13.00", 3, y=380),
    _line("价税合计（小写）￥113.00", 4, y=420),
]


class _StubEngine:
    """确定性 OCR 桩：一张全电发票的完整字段行（20 位发票号，能过校验器）。"""

    version = "stub"

    def recognize(self, _image) -> OcrResult:
        return OcrResult(lines=_STUB_LINES, image_size=(1000, 600), elapsed_ms=1.0)


@pytest.fixture()
def credential_store() -> InMemoryCredentialStore:
    return InMemoryCredentialStore()


@pytest.fixture()
def storage():
    from tests.test_submit import FakeStorage

    return FakeStorage()


@pytest.fixture()
def client(session_factory, storage, credential_store) -> Iterator[TestClient]:
    with _client_for(
        session_factory,
        storage,
        settings_updates={"finance_key_passphrase": FINANCE_PASSPHRASE},
        credential_store=credential_store,
        task_queue=_FakeQueue(),
    ) as test_client:
        yield test_client


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def _pipeline_deps(storage) -> PipelineDeps:
    return PipelineDeps(
        engine=_StubEngine(),
        storage=storage,
        prepare_pages=lambda *args, **kwargs: _fake_pages(1),
        templates=None,
        registry=None,
        clock=lambda: 1.0,
    )


def _upload_and_confirm(
    client, session_factory, storage, actors, *, jpeg: bytes
) -> int:
    uploaded = client.post(
        f"{BASE}/invoices/upload",
        files={"file": ("e2e.jpg", jpeg, "image/jpeg")},
        headers=_auth(actors["alice"]),
    )
    assert uploaded.status_code == 202, uploaded.text
    invoice_id = uploaded.json()["data"]["invoice_id"]
    job_id = uploaded.json()["data"]["job_id"]

    outcome = job_service.execute_job_attempt(
        job_id,
        attempt=1,
        handler=lambda db, job: run_pipeline(db, job, deps=_pipeline_deps(storage)),
        session_factory=session_factory,
        lock_store=_FakeLockStore(),
    )
    assert outcome == "succeeded"

    # 逐字段确认等价于 UI 勾选；仍走确认接口
    with session_factory() as db:
        db.execute(
            sa_text(
                "update invoice_fields set confirmed = true, need_review = false"
                " where invoice_id = :id"
            ),
            {"id": invoice_id},
        )
        db.commit()
        version = db.get(Invoice, invoice_id).version

    confirmed = client.post(
        f"{BASE}/invoices/{invoice_id}/confirm",
        json={"version": version},
        headers=_auth(actors["alice"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    return invoice_id


def _create_report(client, actors, invoice_id: int, amount: str = "113.00") -> int:
    created = client.post(
        f"{BASE}/reports",
        json={"title": "2026-11 学术会议差旅", "reason": "E2E 联调"},
        headers=_auth(actors["alice"]),
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["data"]["id"]
    added = client.post(
        f"{BASE}/reports/{report_id}/items",
        json={"invoice_id": invoice_id, "amount": amount, "remark": "交通"},
        headers=_auth(actors["alice"]),
    )
    assert added.status_code == 201, added.text
    return report_id


def _sign(client, actors, report_id: int):
    return client.post(
        f"{BASE}/reports/{report_id}/sign",
        json={"private_key_passphrase": PASSPHRASE},
        headers=_auth(actors["alice"]),
    )


def _submit(client, actors, report_id: int, key: str):
    return client.post(
        f"{BASE}/reports/{report_id}/submit",
        json={"idempotency_key": key},
        headers=_auth(actors["alice"]),
    )


def _verify(client, actors, report_id: int):
    return client.post(
        f"{BASE}/finance/reports/{report_id}/verify", headers=_auth(actors["rita"])
    )


def _version(session_factory, report_id: int) -> int:
    with session_factory() as db:
        return db.scalar(select(ReportForm.version).where(ReportForm.id == report_id))


# --------------------------------------------------------------------------- #
# 1. 正常全链路 + 安全不变量
# --------------------------------------------------------------------------- #
def test_full_chain_and_security_invariants(
    client, session_factory, storage, actors
) -> None:
    jpeg = _jpeg()
    invoice_id = _upload_and_confirm(client, session_factory, storage, actors, jpeg=jpeg)
    report_id = _create_report(client, actors, invoice_id)
    assert _sign(client, actors, report_id).status_code == 200
    submitted = _submit(client, actors, report_id, "e2e-happy-0001")
    assert submitted.status_code == 200, submitted.text

    # ── 安全不变量（提交后）────────────────────────────────
    with session_factory() as db:
        file_row = db.scalars(
            select(InvoiceFile).where(InvoiceFile.uploader_id == actors["alice"].id)
        ).one()
        form_envelope = db.scalars(
            select(EnvelopeRow).where(EnvelopeRow.form_id == report_id)
        ).one()
        file_envelope = db.scalars(
            select(EnvelopeRow).where(EnvelopeRow.file_id == file_row.id)
        ).one()
        settings = get_settings().model_copy(
            update={"finance_key_passphrase": FINANCE_PASSPHRASE}
        )
        provider = provider_for(settings)
        finance_key = load_finance_private_key(
            db, form_envelope.recipient_cert_id, settings
        )
        cert_fp = db.get(Certificate, form_envelope.recipient_cert_id).fingerprint_sha256

        form_plain = open_sealed(
            provider=provider,
            private_key=finance_key,
            key_algo=form_envelope.key_algo,
            cipher_algo=form_envelope.cipher_algo,
            recipient_cert_fp=cert_fp,
            wrapped_key_b64=form_envelope.wrapped_key_b64,
            iv_b64=form_envelope.iv_b64,
            mac_b64=form_envelope.mac_b64,
            ciphertext=base64.b64decode(form_envelope.ciphertext_b64),
            payload_sha256=form_envelope.payload_sha256,
        )
        file_plain = open_sealed(
            provider=provider,
            private_key=finance_key,
            key_algo=file_envelope.key_algo,
            cipher_algo=file_envelope.cipher_algo,
            recipient_cert_fp=cert_fp,
            wrapped_key_b64=file_envelope.wrapped_key_b64,
            iv_b64=file_envelope.iv_b64,
            mac_b64=file_envelope.mac_b64,
            ciphertext=storage.get_bytes(
                file_envelope.ciphertext_storage_key, max_bytes=10 * 1024 * 1024
            ),
            payload_sha256=file_envelope.payload_sha256,
        )
        assert hashlib.sha256(form_plain).hexdigest() == form_envelope.payload_sha256
        assert hashlib.sha256(file_plain).hexdigest() == file_row.sha256
        assert file_plain == jpeg
        # 库里没有影像字节列
        bytea_columns = db.execute(
            sa_text(
                "select table_name, column_name from information_schema.columns"
                " where table_schema = 'public' and data_type = 'bytea'"
                " and table_name in ('invoices','invoice_files','report_forms')"
            )
        ).all()
        assert bytea_columns == []
        # 对象存储里是密文，不是 JPEG 明文
        ciphertext_object = storage.objects[file_envelope.ciphertext_storage_key]
        assert file_row.is_cipher is True
        assert not ciphertext_object.startswith(b"\xff\xd8")
        assert not ciphertext_object.startswith(b"\x89PNG")

    # ── 财务侧：验签 → 解密 → 预览 ────────────────────────
    verified = _verify(client, actors, report_id)
    assert verified.status_code == 200, verified.text
    data = verified.json()["data"]
    assert data["verified"] is True and len(data["steps"]) == 7
    token = data["verify_token"]

    decrypted = client.post(
        f"{BASE}/finance/reports/{report_id}/decrypt",
        json={"verify_token": token},
        headers=_auth(actors["rita"]),
    )
    assert decrypted.status_code == 200, decrypted.text
    preview = decrypted.json()["data"]
    preview_json = client.get(
        f"{BASE}/finance/preview/{preview['preview_token']}", headers=_auth(actors["rita"])
    )
    assert preview_json.status_code == 200
    assert preview_json.json()["report_no"]
    preview_image = client.get(
        f"{BASE}/finance/preview/{preview['preview_token']}/files/{file_row.id}",
        headers=_auth(actors["rita"]),
    )
    assert preview_image.status_code == 200 and preview_image.content == jpeg
    for response in (verified, decrypted):
        assert "ciphertext_b64" not in response.text
        assert "wrapped_key_b64" not in response.text

    # ── 审核 → 归档 ───────────────────────────────────────
    approved = client.post(
        f"{BASE}/finance/reports/{report_id}/review",
        json={
            "decision": "approve",
            "version": _version(session_factory, report_id),
            "verify_token": token,
        },
        headers=_auth(actors["rita"]),
    )
    assert approved.status_code == 200, approved.text

    archived = client.post(
        f"{BASE}/admin/reports/{report_id}/archive",
        json={"confirm": True},
        headers=_auth(actors["ada"]),
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["data"]["archive_no"].startswith("AR-")
    with session_factory() as db:
        assert db.get(ReportForm, report_id).status == "archived"
        assert verify_chain(db).ok is True


# --------------------------------------------------------------------------- #
# 2. 异常码
# --------------------------------------------------------------------------- #
def test_error_codes_match_contract(client, session_factory, storage, actors) -> None:
    assert client.get(f"{BASE}/finance/pending").status_code == 401
    assert (
        client.get(f"{BASE}/finance/pending", headers=_auth(actors["alice"])).status_code
        == 403
    )

    jpeg = _jpeg()
    invoice_id = _upload_and_confirm(client, session_factory, storage, actors, jpeg=jpeg)
    report_id = _create_report(client, actors, invoice_id)

    wrong = client.post(
        f"{BASE}/reports/{report_id}/sign",
        json={"private_key_passphrase": "definitely-wrong"},
        headers=_auth(actors["alice"]),
    )
    assert wrong.status_code == 422 and wrong.json()["code"] == 42200

    assert _sign(client, actors, report_id).status_code == 200
    assert _submit(client, actors, report_id, "e2e-error-0001").status_code == 200

    repeated = _submit(client, actors, report_id, "e2e-error-0002")
    assert repeated.status_code == 409 and repeated.json()["code"] == 40900

    duplicate_upload = client.post(
        f"{BASE}/invoices/upload",
        files={"file": ("again.jpg", jpeg, "image/jpeg")},
        headers=_auth(actors["alice"]),
    )
    assert duplicate_upload.status_code == 409
    assert duplicate_upload.json()["code"] == 40901

    second_report = client.post(
        f"{BASE}/reports",
        json={"title": "第二张单", "reason": "一票一单"},
        headers=_auth(actors["alice"]),
    ).json()["data"]["id"]
    reused_invoice = client.post(
        f"{BASE}/reports/{second_report}/items",
        json={"invoice_id": invoice_id, "amount": "113.00"},
        headers=_auth(actors["alice"]),
    )
    assert reused_invoice.status_code == 409
    assert reused_invoice.json()["code"] == 40901

    unverified_approve = client.post(
        f"{BASE}/finance/reports/{report_id}/review",
        json={
            "decision": "approve",
            "version": _version(session_factory, report_id),
            "verify_token": "never-issued-token",
        },
        headers=_auth(actors["rita"]),
    )
    assert unverified_approve.status_code == 409
    assert unverified_approve.json()["code"] == 40900

    too_early_archive = client.post(
        f"{BASE}/admin/reports/{report_id}/archive",
        json={"confirm": True},
        headers=_auth(actors["ada"]),
    )
    assert too_early_archive.status_code == 409

    # 篡改签名域 → verify 必须失败且归类为验签错误
    with session_factory() as db:
        db.execute(
            sa_text("update report_forms set canonical_json = canonical_json || ' ' where id = :id"),
            {"id": report_id},
        )
        db.commit()
    tampered = _verify(client, actors, report_id)
    assert tampered.status_code == 409
    assert tampered.json()["code"] == 50021
    assert tampered.json()["data"]["reason_code"] in {"bad_signature", "digest_mismatch"}

    # 归档缺 confirm
    missing_confirm = client.post(
        f"{BASE}/admin/reports/{report_id}/archive",
        json={"confirm": False},
        headers=_auth(actors["ada"]),
    )
    assert missing_confirm.status_code == 422
    assert missing_confirm.json()["code"] == 42202


def test_error_codes_are_documented() -> None:
    api_doc = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")
    for code in ("40101", "40301", "40900", "40901", "42200", "42202", "50021"):
        assert f"| {code} |" in api_doc, f"{code} 未登记在 API.md 错误码表"


# --------------------------------------------------------------------------- #
# 3. 并发 approve
# --------------------------------------------------------------------------- #
def test_concurrent_approve_one_winner(
    client, session_factory, storage, actors, credential_store
) -> None:
    jpeg = _jpeg()
    invoice_id = _upload_and_confirm(client, session_factory, storage, actors, jpeg=jpeg)
    report_id = _create_report(client, actors, invoice_id)
    assert _sign(client, actors, report_id).status_code == 200
    assert _submit(client, actors, report_id, "e2e-concurrent-0001").status_code == 200

    token = _verify(client, actors, report_id).json()["data"]["verify_token"]
    version = _version(session_factory, report_id)
    payload = {"decision": "approve", "version": version, "verify_token": token}

    second = _client_for(
        session_factory,
        storage,
        settings_updates={"finance_key_passphrase": FINANCE_PASSPHRASE},
        credential_store=credential_store,
        task_queue=_FakeQueue(),
    )

    def _approve(c: TestClient) -> int:
        return c.post(
            f"{BASE}/finance/reports/{report_id}/review",
            json=payload,
            headers=_auth(actors["rita"]),
        ).status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(_approve, (client, second)))
    finally:
        second.close()

    assert statuses == [200, 409]
    with session_factory() as db:
        assert db.get(ReportForm, report_id).status == "approved"
        assert db.scalar(select(func.count()).select_from(ReviewLog)) == 1
        assert credential_store.get(verify_key(report_id)) is None