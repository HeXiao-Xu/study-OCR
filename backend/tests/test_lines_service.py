"""S3-5 行框：原图坐标映射、字段归属、绝不返回文本。"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from decimal import Decimal

import numpy as np
import pytest
from alembic import command
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.exceptions import ConflictError, ValidationFailedError
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.ocr_lines import OcrLine
from app.models.users import User
from app.services import invoice_service, line_service
from tests.db_helpers import alembic_config, temporary_database

pytestmark = pytest.mark.integration

_BASE = dict(
    database_url="postgresql://u:p@127.0.0.1:5433/db",
    redis_url="redis://:p@127.0.0.1:6380/0",
    minio_endpoint="127.0.0.1:9002", minio_access_key="k", minio_secret_key="s",
    minio_bucket="b", jwt_secret="s" * 32,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_BASE, **overrides})


class _Prepared:
    class _Preprocess:
        to_original = np.array([[0.5, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 0.0, 1.0]])

    preprocess = _Preprocess()


class FakeStorage:
    def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        return b"fake-image"


@pytest.fixture(scope="module")
def lines_database() -> Iterator[URL]:
    with temporary_database("study_ocr_lines_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(lines_database: URL):
    eng = create_engine(lines_database.render_as_string(hide_password=False), poolclass=NullPool)
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
def alice(session_factory, clean_db) -> User:
    with session_factory() as db:
        user = User(username="alice", real_name="A", role="submitter", status="active")
        db.add(user)
        db.commit()
        return user


def _make_invoice_with_line(session_factory, uploader_id: int, *, line_box=None,
                            field_name: str | None = None,
                            is_cipher: bool = False) -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin",
                           is_cipher=is_cipher, page_count=1)
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=uploader_id, file_id=file.id)
        db.add(OcrLine(invoice_id=invoice.id, page_no=1, line_no=0,
                       text="价税合计 ￥1,234.56", score=Decimal("0.950"),
                       box_json=line_box or [[100, 300], [900, 300], [900, 400], [100, 400]]))
        if field_name:
            db.add(InvoiceField(invoice_id=invoice.id, field_name=field_name,
                                field_value="￥1,234.56", normalized_value="1234.56",
                                raw_text="价税合计 ￥1,234.56", source="ocr",
                                confidence=Decimal("0.700"), bbox_json=[50, 150, 450, 200],
                                source_page_no=1, source_line_no=0))
        db.commit()
        return invoice.id


def test_lines_map_to_original_and_link_field(
    session_factory, alice, monkeypatch
) -> None:
    monkeypatch.setattr(line_service, "prepare_input", lambda data: _Prepared())
    invoice_id = _make_invoice_with_line(
        session_factory, alice.id,
        line_box=[[10, 20], [300, 20], [300, 60], [10, 60]],   # 校正图四点
        field_name="total_amount",
    )

    with session_factory() as db:
        data = line_service.get_invoice_lines(
            db, db.get(User, alice.id), invoice_id, page=1,
            settings=_settings(), storage=FakeStorage(),
        )

    assert data.page_no == 1
    assert data.lines[0].line_no == 0
    assert data.lines[0].bbox == [5, 10, 150, 30]        # 0.5× 映射后的原图外接框
    assert data.lines[0].field_name == "total_amount"
    assert "text" not in data.lines[0].model_dump()       # 不返回票面文本


def test_lines_page_out_of_range_rejected(session_factory, alice) -> None:
    invoice_id = _make_invoice_with_line(session_factory, alice.id)

    with session_factory() as db:
        with pytest.raises(ValidationFailedError):
            line_service.get_invoice_lines(
                db, db.get(User, alice.id), invoice_id, page=2,
                settings=_settings(), storage=FakeStorage(),
            )


def test_lines_cipher_rejected(session_factory, alice) -> None:
    invoice_id = _make_invoice_with_line(session_factory, alice.id, is_cipher=True)

    with session_factory() as db:
        with pytest.raises(ConflictError):
            line_service.get_invoice_lines(
                db, db.get(User, alice.id), invoice_id, page=1,
                settings=_settings(), storage=FakeStorage(),
            )


def test_lines_skip_malformed_boxes(session_factory, alice, monkeypatch) -> None:
    monkeypatch.setattr(line_service, "prepare_input", lambda data: _Prepared())
    invoice_id = _make_invoice_with_line(
        session_factory, alice.id, line_box=[[10, 20], [300, 20]]
    )

    with session_factory() as db:
        data = line_service.get_invoice_lines(
            db, db.get(User, alice.id), invoice_id, page=1,
            settings=_settings(), storage=FakeStorage(),
        )

    assert data.lines == []          # 畸形框跳过，不制造假框