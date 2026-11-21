"""S5-2 签名 → 加密提交链路：HTTP 集成（临时库 + 假 MinIO + Docker OpenSSL 现场生成 SM 身份）。

跑法：`cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_submit.py -m integration -q`

为什么用 Docker 现生成身份：私钥不进仓库（连测试夹具也不进），而运行期本来就不做证书链验签
（D-080），自签 SM2 证书足够；Docker/OpenSSL 不可用时整模块 skip（不制造假绿）。

RSA 对照路径的身份用 `cryptography` 生成（自签证书），不需要 Docker。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
import shutil
import subprocess
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.api.deps import get_task_queue
from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import AppError, NotFoundError, PayloadTooLargeError
from app.core.security import TokenType, encode_token
from app.crypto.certs import parse_certificate
from app.crypto.keystore import private_key_hex_from_pkcs8, wrap_private_key
from app.crypto.key_loader import load_key_material
from app.crypto.provider import SMProvider, get_provider
from app.crypto.sm.sm2 import public_key_from_private
from app.main import create_app
from app.models.audit_logs import AuditLog
from app.models.certificates import Certificate
from app.models.envelopes import Envelope
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.key_records import KeyRecord
from app.models.report_forms import ReportForm
from app.models.signatures import Signature
from app.models.users import User
from app.schemas.report import SubmitRequest
from app.services import submit_service
from app.services.audit import verify_chain
from app.services.report_crypto import open_sealed
from app.services.storage import get_storage
from app.services.credential_store import get_credential_store
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/reports"
REPO = Path(__file__).resolve().parents[2]
PASSPHRASE = "test-pass-1234"
OPENSSL_IMAGE = "alpine/openssl:3.5.9"
pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 数据库装置
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def submit_database() -> Iterator[URL]:
    with temporary_database("study_ocr_submit_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(submit_database: URL):
    eng = create_engine(
        submit_database.render_as_string(hide_password=False), poolclass=NullPool
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
            sa_text(
                "TRUNCATE users, certificates, key_records RESTART IDENTITY CASCADE"
            )
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


# --------------------------------------------------------------------------- #
# 假对象存储
# --------------------------------------------------------------------------- #
class FakeStorage:
    """与 `StorageService` 同签名；对象只放内存，便于断言"明文被删/密文可解"。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.modified: dict[str, datetime] = {}

    def build_object_key(self) -> str:
        return f"invoices/test/{secrets.token_hex(8)}.bin"

    def put(self, key: str, fileobj, length: int, content_type: str) -> None:
        data = fileobj.read()
        assert len(data) == length
        self.objects[key] = data
        self.modified[key] = datetime.now(timezone.utc)

    def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        if key not in self.objects:
            raise NotFoundError("票据文件对象不存在")
        data = self.objects[key]
        if len(data) > max_bytes:
            raise PayloadTooLargeError("票据文件超过读取上限")
        return data

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.modified.pop(key, None)
        self.deleted.append(key)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def list_objects(self, prefix: str) -> list[tuple[str, datetime]]:
        now = datetime.now(timezone.utc)
        return [
            (key, self.modified.get(key, now))
            for key in sorted(self.objects)
            if key.startswith(prefix)
        ]


@pytest.fixture()
def storage() -> FakeStorage:
    return FakeStorage()


def _client_for(
    session_factory,
    storage,
    *,
    algo: str | None = None,
    settings_updates: dict | None = None,
    credential_store=None,
    task_queue=None,
) -> TestClient:
    base = get_settings()
    updates = dict(settings_updates or {})
    if algo:
        updates["algo_provider"] = algo
    settings = base.model_copy(update=updates) if updates else base
    app = create_app(settings=settings)

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_settings] = lambda: settings
    if credential_store is not None:
        app.dependency_overrides[get_credential_store] = lambda: credential_store
    if task_queue is not None:
        app.dependency_overrides[get_task_queue] = lambda: task_queue
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def client(session_factory, storage) -> Iterator[TestClient]:
    with _client_for(session_factory, storage) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 身份（SM 用 Docker 现生成；RSA 用 cryptography）
# --------------------------------------------------------------------------- #
def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _docker_openssl(*args: str) -> None:
    completed = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{REPO}:/work", "-w", "/work",
            "--entrypoint", "openssl", OPENSSL_IMAGE, *args,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(
            "Docker/OpenSSL 不可用，跳过 SM 身份相关用例："
            + (completed.stderr or completed.stdout or "")[:200]
        )


def _pem_to_der(pem: bytes) -> bytes:
    text = pem.decode("ascii").strip()
    lines = [line for line in text.splitlines() if line and not line.startswith("-----")]
    return base64.b64decode("".join(lines), validate=True)


def _generate_sm_identity(work: Path, name: str, role: str) -> dict:
    key_pem_path = work / f"{name}-gen.pem"
    cert_path = work / f"{name}.crt.pem"
    pub_path = work / f"{name}.pub.pem"
    _docker_openssl(
        "req", "-x509", "-newkey", "sm2", "-sm3", "-nodes", "-days", "365",
        "-keyout", _rel(key_pem_path), "-out", _rel(cert_path),
        "-subj", f"/CN={name}/O=StudyOCR Test/OU={role}",
    )
    # cryptography 不认 SM2 公钥（certs.py 注释里的已知限制），pub PEM 让 OpenSSL 导出
    _docker_openssl("pkey", "-in", _rel(key_pem_path), "-pubout", "-out", _rel(pub_path))
    key_der = _pem_to_der(key_pem_path.read_bytes())
    key_pem_path.unlink()
    record = wrap_private_key(key_der, PASSPHRASE)
    key_path = work / f"{name}-sign.p8.enc"
    key_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    private_hex = private_key_hex_from_pkcs8(key_der)
    cert_pem = cert_path.read_bytes()
    return {
        "cert_pem": cert_pem,
        "public_key_pem": pub_path.read_text(encoding="ascii"),
        "key_path": key_path,
        "private_hex": private_hex,
        "public_hex": public_key_from_private(private_hex),
        "fingerprint": hashlib.sha256(x509.load_pem_x509_certificate(cert_pem).public_bytes(
            serialization.Encoding.DER
        )).hexdigest(),
    }


@pytest.fixture(scope="module")
def sm_identities() -> Iterator[dict[str, dict]]:
    work = REPO / "backend/tests/.tmp-s5-identity"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        yield {
            "alice": _generate_sm_identity(work, "alice", "submitter"),
            "finance": _generate_sm_identity(work, "finance", "server"),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _generate_rsa_identity(work: Path, name: str) -> dict:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "StudyOCR Test"),
        ]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_path = work / f"{name}-rsa.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(PASSPHRASE.encode("utf-8")),
        )
    )
    return {"cert_pem": cert_pem, "key_path": key_path}


@pytest.fixture(scope="module")
def rsa_identities(tmp_path_factory) -> dict[str, dict]:
    work = tmp_path_factory.mktemp("s5-rsa-identities")
    return {
        "alice": _generate_rsa_identity(work, "alice"),
        "finance": _generate_rsa_identity(work, "finance"),
    }


def _cert_fields(cert_pem: bytes, public_key_pem: str | None = None) -> dict:
    cert = x509.load_pem_x509_certificate(cert_pem)
    der = cert.public_bytes(serialization.Encoding.DER)
    if public_key_pem is None:
        public_key_pem = cert.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii")
    return {
        "subject_dn": cert.subject.rfc4514_string(),
        "issuer_dn": cert.issuer.rfc4514_string(),
        "serial_no": format(cert.serial_number, "x"),
        "not_before": cert.not_valid_before_utc,
        "not_after": cert.not_valid_after_utc,
        "fingerprint_sha256": hashlib.sha256(der).hexdigest(),
        "public_key_pem": public_key_pem,
    }


def _register_identities(
    session_factory, user: User, identities: dict[str, dict], *, algo: str
) -> None:
    with session_factory() as db:
        u = db.get(User, user.id)
        user_fields = _cert_fields(
            identities["alice"]["cert_pem"], identities["alice"].get("public_key_pem")
        )
        user_cert = Certificate(
            owner_type="user", owner_id=u.id, key_algo=algo, revoked=False,
            cert_pem=identities["alice"]["cert_pem"].decode("ascii"),
            **user_fields,
        )
        db.add(user_cert)
        db.flush()
        db.add(
            KeyRecord(
                user_id=u.id, key_type="sign", cert_id=user_cert.id,
                storage_path=str(identities["alice"]["key_path"]), algo=algo,
            )
        )
        finance_fields = _cert_fields(
            identities["finance"]["cert_pem"],
            identities["finance"].get("public_key_pem"),
        )
        finance_cert = Certificate(
            owner_type="server", owner_id=None, key_algo=algo, revoked=False,
            cert_pem=identities["finance"]["cert_pem"].decode("ascii"),
            **finance_fields,
        )
        db.add(finance_cert)
        db.flush()
        db.add(
            KeyRecord(
                user_id=None, key_type="envelope", cert_id=finance_cert.id,
                storage_path=str(identities["finance"]["key_path"]), algo=algo,
            )
        )
        db.commit()


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(
        user_id=user.id, role=user.role, token_type=TokenType.ACCESS, ttl_seconds=60
    )
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# 单据/票据工厂
# --------------------------------------------------------------------------- #
def _make_invoice(
    session_factory,
    storage: FakeStorage,
    owner: User,
    *,
    total: str = "100.00",
    plaintext: bytes | None = None,
) -> int:
    # 每个文件内容唯一（sha256 唯一约束）；需要断言的用例自己传 plaintext
    plaintext = plaintext or f"plain-image-{secrets.token_hex(6)}".encode("utf-8")
    key = f"invoices/test/{secrets.token_hex(8)}.bin"
    storage.put(key, io.BytesIO(plaintext), len(plaintext), "image/jpeg")
    with session_factory() as db:
        file_row = InvoiceFile(
            uploader_id=owner.id, filename="a.jpg", mime="image/jpeg",
            size=len(plaintext), sha256=hashlib.sha256(plaintext).hexdigest(),
            storage_key=key, is_cipher=False, page_count=1,
        )
        db.add(file_row)
        db.flush()
        invoice = Invoice(
            uploader_id=owner.id, file_id=file_row.id, invoice_type="vat_general",
            status="confirmed", ocr_status="succeeded", extract_status="confirmed",
            invoice_code="1234567890", invoice_no=f"{secrets.randbelow(10**8):08d}",
            amount=Decimal(total), tax_amount=Decimal("0.00"), total_amount=Decimal(total),
        )
        db.add(invoice)
        db.flush()
        db.add(
            InvoiceField(
                invoice_id=invoice.id, field_name="total_amount",
                field_value=total, normalized_value=total, raw_text=total,
                source="ocr", confidence=Decimal("0.990"), confirmed=True,
            )
        )
        db.commit()
        return invoice.id


def _report_with_item(client, actors, session_factory, storage, *, owner="alice", total="100.00"):
    invoice_id = _make_invoice(session_factory, storage, actors[owner], total=total)
    created = client.post(
        BASE,
        json={"title": "2026-11 差旅报销", "reason": "学术会议"},
        headers=_auth(actors[owner]),
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["data"]["id"]
    added = client.post(
        f"{BASE}/{report_id}/items",
        json={"invoice_id": invoice_id, "amount": total, "remark": "交通"},
        headers=_auth(actors[owner]),
    )
    assert added.status_code == 201, added.text
    return report_id, invoice_id


def _sign(client, user, report_id: int, *, passphrase: str = PASSPHRASE):
    return client.post(
        f"{BASE}/{report_id}/sign",
        json={"private_key_passphrase": passphrase},
        headers=_auth(user),
    )


def _submit(client, user, report_id: int, key: str, *, version: int | None = None):
    body = {"idempotency_key": key}
    if version is not None:
        body["version"] = version
    return client.post(f"{BASE}/{report_id}/submit", json=body, headers=_auth(user))


def _signed_report(client, actors, session_factory, storage, sm_identities, *, owner="alice", total="100.00"):
    _register_identities(session_factory, actors[owner], sm_identities, algo="SM2")
    report_id, invoice_id = _report_with_item(
        client, actors, session_factory, storage, owner=owner, total=total
    )
    signed = _sign(client, actors[owner], report_id)
    assert signed.status_code == 200, signed.text
    return report_id, invoice_id


# --------------------------------------------------------------------------- #
# 签名
# --------------------------------------------------------------------------- #
def test_sign_success(client, session_factory, storage, actors, sm_identities) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_id, _ = _report_with_item(client, actors, session_factory, storage)

    response = _sign(client, actors["alice"], report_id)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "signed"
    assert data["digest_algo"] == "SM3"
    assert data["algo"] == "SM2"
    assert data["cert_id"] > 0
    assert len(data["digest_hex"]) == 64
    assert len(data["canonical_sha256"]) == 64
    # 响应里没有私钥/口令
    assert "passphrase" not in response.text
    assert "PRIVATE" not in response.text

    with session_factory() as db:
        form = db.get(ReportForm, report_id)
        assert form.status == "signed"
        assert form.canonical_json is not None
        assert form.digest_hex == data["digest_hex"]
        # 用库里那份 canonical 字节独立复算
        assert hashlib.sha256(form.canonical_json.encode("utf-8")).hexdigest() == data["canonical_sha256"]
        from app.crypto.sm.sm3 import sm3_digest

        assert sm3_digest(form.canonical_json.encode("utf-8")).hex() == data["digest_hex"]

        signature = db.scalars(
            select(Signature).where(Signature.form_id == report_id)
        ).one()
        assert signature.status == "active"
        assert signature.version == 1
        assert signature.verified is False
        assert signature.algo == "SM2"

        audit = db.scalars(select(AuditLog).where(AuditLog.action == "sign")).all()
        assert len(audit) == 1 and audit[0].target_type == "signature"
        assert audit[0].detail_json["report_no"] == form.report_no
        assert verify_chain(db).ok is True

        # 独立验签：证书公钥验 canonical 字节
        provider = get_provider("sm")
        pub = parse_certificate(sm_identities["alice"]["cert_pem"]).public_key_hex
        key_material = SMProvider.key_from_hex(sm_identities["alice"]["private_hex"], pub)
        assert provider.verify(
            form.canonical_json.encode("utf-8"),
            base64.b64decode(signature.signature_b64),
            key_material,
        )


def test_sign_requires_items(client, session_factory, actors, sm_identities) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    created = client.post(
        BASE, json={"title": "t", "reason": "r"}, headers=_auth(actors["alice"])
    )
    report_id = created.json()["data"]["id"]

    response = _sign(client, actors["alice"], report_id)

    assert response.status_code == 409 and response.json()["code"] == 40900
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Signature)) == 0


def test_sign_rejects_unconfirmed_invoice(
    client, session_factory, storage, actors, sm_identities
) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_id, invoice_id = _report_with_item(client, actors, session_factory, storage)
    with session_factory() as db:
        db.execute(
            sa_text("update invoices set extract_status = 'need_confirm' where id = :id"),
            {"id": invoice_id},
        )
        db.commit()

    response = _sign(client, actors["alice"], report_id)

    assert response.status_code == 409 and response.json()["code"] == 40900
    assert "未完成确认" in response.json()["message"]


def test_sign_rejects_submitted_form(
    client, session_factory, storage, actors, sm_identities
) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_id, _ = _report_with_item(client, actors, session_factory, storage)
    with session_factory() as db:
        db.execute(
            sa_text("update report_forms set status = 'submitted' where id = :id"),
            {"id": report_id},
        )
        db.commit()

    response = _sign(client, actors["alice"], report_id)

    assert response.status_code == 409


def test_sign_permissions(client, session_factory, storage, actors, sm_identities) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_id, _ = _report_with_item(client, actors, session_factory, storage)

    assert _sign(client, actors["bob"], report_id).status_code == 404
    # reviewer 看不见草稿（行级隔离）；admin 一律 404
    assert _sign(client, actors["rita"], report_id).status_code == 404
    assert _sign(client, actors["ada"], report_id).status_code == 404

    # 单据对 reviewer 可见之后，角色级拒绝是 403（不是 404）
    with session_factory() as db:
        db.execute(
            sa_text("update report_forms set status = 'submitted' where id = :id"),
            {"id": report_id},
        )
        db.commit()
    forbidden = _sign(client, actors["rita"], report_id)
    assert forbidden.status_code == 403 and forbidden.json()["code"] == 40301


def test_sign_wrong_passphrase_is_uniform_422(
    client, session_factory, storage, actors, sm_identities
) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_id, _ = _report_with_item(client, actors, session_factory, storage)

    response = _sign(client, actors["alice"], report_id, passphrase="definitely-wrong")

    assert response.status_code == 422 and response.json()["code"] == 42200
    assert response.json()["message"] == "私钥口令错误或密钥不可用"
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Signature)) == 0
        assert db.get(ReportForm, report_id).status == "draft"


def test_sign_without_certificate_is_409(
    client, session_factory, storage, actors
) -> None:
    report_id, _ = _report_with_item(client, actors, session_factory, storage)

    response = _sign(client, actors["alice"], report_id)

    assert response.status_code == 409 and response.json()["code"] == 40900


def test_sign_revoked_certificate_is_409(
    client, session_factory, storage, actors, sm_identities
) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_id, _ = _report_with_item(client, actors, session_factory, storage)
    with session_factory() as db:
        db.execute(sa_text("update certificates set revoked = true, revoked_at = now()"))
        db.commit()

    response = _sign(client, actors["alice"], report_id)

    assert response.status_code == 409
    assert "cert_revoked" in response.json()["message"]


def test_resign_voids_previous_signature(
    client, session_factory, storage, actors, sm_identities
) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    report_id, _ = _report_with_item(client, actors, session_factory, storage)

    first = _sign(client, actors["alice"], report_id)
    assert first.status_code == 200
    second = _sign(client, actors["alice"], report_id)
    assert second.status_code == 200, second.text

    with session_factory() as db:
        rows = db.scalars(
            select(Signature).where(Signature.form_id == report_id).order_by(Signature.version)
        ).all()
        assert [row.status for row in rows] == ["void", "active"]
        assert [row.version for row in rows] == [1, 2]
        form = db.get(ReportForm, report_id)
        # 0（建单）→ 1（加明细）→ 2（首签）→ 3（重签）
        assert form.version == 3
        assert first.json()["data"]["digest_hex"] != second.json()["data"]["digest_hex"]


# --------------------------------------------------------------------------- #
# 提交
# --------------------------------------------------------------------------- #
def test_submit_success_encrypts_images_and_seals_report(
    client, session_factory, storage, actors, sm_identities
) -> None:
    report_id, invoice_id = _signed_report(
        client, actors, session_factory, storage, sm_identities
    )
    with session_factory() as db:
        form_before = db.get(ReportForm, report_id)
        file_before = db.get(InvoiceFile, db.get(Invoice, invoice_id).file_id)
        plaintext_key = file_before.storage_key
        expected_plaintext = storage.objects[plaintext_key]
        assert file_before.is_cipher is False

    response = _submit(client, actors["alice"], report_id, "idem-key-0001")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["duplicated"] is False
    assert data["report_no"] == form_before.report_no
    assert data["status"] == "submitted"
    assert data["submitted_at"] is not None
    assert "ciphertext" not in response.text and "wrapped_key" not in response.text

    with session_factory() as db:
        form = db.get(ReportForm, report_id)
        assert form.status == "submitted"
        assert form.submitted_at is not None
        assert form.idempotency_key == "idem-key-0001"

        envelopes = db.scalars(select(Envelope)).all()
        form_envs = [row for row in envelopes if row.form_id == report_id]
        file_envs = [row for row in envelopes if row.file_id is not None]
        assert len(form_envs) == 1 and len(file_envs) == 1
        form_env = form_envs[0]
        file_env = file_envs[0]
        for row in envelopes:
            assert row.wrapped_key_b64 and row.iv_b64 and row.mac_b64
            assert len(row.payload_sha256) == 64
            assert row.recipient_cert_id > 0
        assert form_env.ciphertext_b64 and form_env.ciphertext_storage_key is None
        assert file_env.ciphertext_storage_key and file_env.ciphertext_b64 is None

        file_row = db.get(InvoiceFile, db.get(Invoice, invoice_id).file_id)
        assert file_row.is_cipher is True
        assert file_row.storage_key == file_env.ciphertext_storage_key
        assert file_row.sha256 == hashlib.sha256(expected_plaintext).hexdigest()

        audit_actions = set(
            db.scalars(select(AuditLog.action).where(AuditLog.action.in_(["seal", "submit"]))).all()
        )
        assert audit_actions == {"seal", "submit"}
        # SCHEMA §5.3："submitted 之后必须已验证"——提交即系统自动验签，结论落库
        signature = db.scalars(
            select(Signature).where(Signature.form_id == report_id, Signature.status == "active")
        ).one()
        assert (signature.verified, signature.verify_result) == (True, "ok")
        assert signature.verified_at is not None
        assert signature.verifier_id is None
        assert verify_chain(db).ok is True

    # 明文对象已删除，密文对象可被财务私钥解回原文
    assert plaintext_key not in storage.objects
    assert plaintext_key in storage.deleted
    finance_key = load_key_material(
        key_algo="SM2", storage_path=sm_identities["finance"]["key_path"], passphrase=PASSPHRASE
    )
    plaintext = open_sealed(
        provider=SMProvider(),
        private_key=finance_key,
        key_algo=file_env.key_algo,
        cipher_algo=file_env.cipher_algo,
        recipient_cert_fp=sm_identities["finance"]["fingerprint"],
        wrapped_key_b64=file_env.wrapped_key_b64,
        iv_b64=file_env.iv_b64,
        mac_b64=file_env.mac_b64,
        ciphertext=storage.objects[file_env.ciphertext_storage_key],
        payload_sha256=file_env.payload_sha256,
    )
    assert plaintext == expected_plaintext

    sealed_report = open_sealed(
        provider=SMProvider(),
        private_key=finance_key,
        key_algo=form_env.key_algo,
        cipher_algo=form_env.cipher_algo,
        recipient_cert_fp=sm_identities["finance"]["fingerprint"],
        wrapped_key_b64=form_env.wrapped_key_b64,
        iv_b64=form_env.iv_b64,
        mac_b64=form_env.mac_b64,
        ciphertext=base64.b64decode(form_env.ciphertext_b64),
        payload_sha256=form_env.payload_sha256,
    )
    payload = json.loads(sealed_report)
    assert payload["report_no"] == data["report_no"]
    assert payload["items"][0]["file_sha256"] == hashlib.sha256(expected_plaintext).hexdigest()
    assert payload["signature"]["digest_hex"]

    # 提交后票据影像接口回到"密文 409"（S5-4 才做内存解密）
    image = client.get(f"/api/v1/invoices/{invoice_id}/image", headers=_auth(actors["alice"]))
    assert image.status_code == 409


def test_submit_requires_signature(client, session_factory, storage, actors) -> None:
    report_id, _ = _report_with_item(client, actors, session_factory, storage)

    response = _submit(client, actors["alice"], report_id, "idem-key-0002")

    assert response.status_code == 409 and response.json()["code"] == 40900
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Envelope)) == 0


def test_submit_detects_tampered_canonical(
    client, session_factory, storage, actors, sm_identities
) -> None:
    report_id, invoice_id = _signed_report(
        client, actors, session_factory, storage, sm_identities
    )
    with session_factory() as db:
        db.execute(
            sa_text(
                "update report_forms set canonical_json = canonical_json || ' ' where id = :id"
            ),
            {"id": report_id},
        )
        db.commit()

    response = _submit(client, actors["alice"], report_id, "idem-key-0003")

    assert response.status_code == 409
    assert response.json()["data"]["reason"] == "digest_mismatch"
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Envelope)) == 0
        assert db.get(ReportForm, report_id).status == "signed"
        file_row = db.get(InvoiceFile, db.get(Invoice, invoice_id).file_id)
        assert file_row.is_cipher is False


def test_submit_idempotent_with_same_key(
    client, session_factory, storage, actors, sm_identities
) -> None:
    report_id, _ = _signed_report(client, actors, session_factory, storage, sm_identities)
    first = _submit(client, actors["alice"], report_id, "idem-key-0004")
    assert first.status_code == 200, first.text

    second = _submit(client, actors["alice"], report_id, "idem-key-0004")

    assert second.status_code == 200, second.text
    assert second.json()["data"]["duplicated"] is True
    assert second.json()["data"]["envelope_id"] == first.json()["data"]["envelope_id"]
    assert second.json()["data"]["signature_id"] == first.json()["data"]["signature_id"]
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Envelope)) == 2   # 单据 + 单文件


def test_submit_rejects_key_used_by_other_report(
    client, session_factory, storage, actors, sm_identities
) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    first_report = _report_with_item(client, actors, session_factory, storage)[0]
    second_report = _report_with_item(client, actors, session_factory, storage)[0]
    assert _sign(client, actors["alice"], first_report).status_code == 200
    assert _sign(client, actors["alice"], second_report).status_code == 200
    assert _submit(client, actors["alice"], first_report, "idem-key-0005").status_code == 200

    response = _submit(client, actors["alice"], second_report, "idem-key-0005")

    assert response.status_code == 409
    assert response.json()["data"]["reason"] == "idempotency_key_reused"


def test_submit_version_mismatch(
    client, session_factory, storage, actors, sm_identities
) -> None:
    report_id, _ = _signed_report(client, actors, session_factory, storage, sm_identities)

    response = _submit(client, actors["alice"], report_id, "idem-key-0006", version=999)

    assert response.status_code == 409


def test_submit_without_finance_certificate_is_409(
    client, session_factory, storage, actors, sm_identities
) -> None:
    _register_identities(session_factory, actors["alice"], sm_identities, algo="SM2")
    with session_factory() as db:
        db.execute(sa_text("delete from key_records where key_type = 'envelope'"))
        db.execute(sa_text("delete from certificates where owner_type = 'server'"))
        db.commit()
    report_id, invoice_id = _report_with_item(client, actors, session_factory, storage)
    assert _sign(client, actors["alice"], report_id).status_code == 200

    response = _submit(client, actors["alice"], report_id, "idem-key-0007")

    assert response.status_code == 409 and response.json()["code"] == 40900
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Envelope)) == 0
        file_row = db.get(InvoiceFile, db.get(Invoice, invoice_id).file_id)
        assert file_row.is_cipher is False


def test_concurrent_submit_keeps_single_envelope_set(
    client, session_factory, storage, actors, sm_identities
) -> None:
    report_id, _ = _signed_report(client, actors, session_factory, storage, sm_identities)
    settings = get_settings()
    barrier = Barrier(2)

    def _worker(key: str) -> str:
        db = session_factory()
        try:
            user = db.get(User, actors["alice"].id)
            barrier.wait(timeout=10)
            submit_service.submit_report(
                db,
                user,
                report_id,
                SubmitRequest(idempotency_key=key),
                settings=settings,
                storage=storage,
            )
            return "ok"
        except AppError:
            return "conflict"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_worker, ["idem-concurrent-a", "idem-concurrent-b"]))

    assert sorted(results) == ["conflict", "ok"]
    with session_factory() as db:
        assert db.get(ReportForm, report_id).status == "submitted"
        assert db.scalar(select(func.count()).select_from(Envelope)) == 2


# --------------------------------------------------------------------------- #
# 详情 / RSA 对照 provider
# --------------------------------------------------------------------------- #
def test_detail_exposes_metadata_but_no_secrets(
    client, session_factory, storage, actors, sm_identities
) -> None:
    report_id, _ = _signed_report(client, actors, session_factory, storage, sm_identities)
    assert _submit(client, actors["alice"], report_id, "idem-key-0008").status_code == 200

    response = client.get(f"{BASE}/{report_id}", headers=_auth(actors["alice"]))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["signature"]["algo"] == "SM2"
    assert data["envelope"]["key_algo"] == "SM2"
    assert data["envelope"]["recipient_cert_fp"]
    assert data["envelope"]["wrapped_key_len"] and data["envelope"]["wrapped_key_len"] > 0
    # 提交即系统自动验签（SCHEMA §5.3 submitted 之后必须已验证）；S5-4 财务端独立复验
    assert data["verify_status"] == "ok"
    for forbidden in ("ciphertext_b64", "wrapped_key_b64", "ciphertext_storage_key"):
        assert forbidden not in response.text


def test_rsa_provider_sign_and_submit(
    session_factory, storage, actors, rsa_identities
) -> None:
    with _client_for(session_factory, storage, algo="rsa") as client:
        _register_identities(session_factory, actors["alice"], rsa_identities, algo="RSA")
        report_id, invoice_id = _report_with_item(client, actors, session_factory, storage)
        with session_factory() as db:
            file_row = db.get(InvoiceFile, db.get(Invoice, invoice_id).file_id)
            expected_plaintext = storage.objects[file_row.storage_key]

        signed = _sign(client, actors["alice"], report_id)
        assert signed.status_code == 200, signed.text
        assert signed.json()["data"]["algo"] == "RSA-PSS"

        submitted = _submit(client, actors["alice"], report_id, "idem-rsa-0001")
        assert submitted.status_code == 200, submitted.text

        with session_factory() as db:
            envelopes = db.scalars(select(Envelope)).all()
            assert {row.key_algo for row in envelopes} == {"RSA-OAEP"}
            assert {row.cipher_algo for row in envelopes} == {"AES-256-GCM"}
            assert all(len(base64.b64decode(row.iv_b64)) == 12 for row in envelopes)
            assert all(len(base64.b64decode(row.mac_b64)) == 16 for row in envelopes)
            form_env = next(row for row in envelopes if row.form_id == report_id)
            file_env = next(row for row in envelopes if row.file_id is not None)

        finance_key = load_key_material(
            key_algo="RSA",
            storage_path=rsa_identities["finance"]["key_path"],
            passphrase=PASSPHRASE,
        )
        provider = get_provider("rsa")
        plaintext = open_sealed(
            provider=provider,
            private_key=finance_key,
            key_algo=file_env.key_algo,
            cipher_algo=file_env.cipher_algo,
            recipient_cert_fp=_cert_fields(
                rsa_identities["finance"]["cert_pem"]
            )["fingerprint_sha256"],
            wrapped_key_b64=file_env.wrapped_key_b64,
            iv_b64=file_env.iv_b64,
            mac_b64=file_env.mac_b64,
            ciphertext=storage.objects[file_env.ciphertext_storage_key],
            payload_sha256=file_env.payload_sha256,
        )
        assert plaintext == expected_plaintext
        assert form_env.payload_sha256