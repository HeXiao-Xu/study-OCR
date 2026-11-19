"""模板与规则管理测试（S2-8）。

单元（默认跑）：入参校验、YAML seed 构建。
集成（`-m integration`）：权限、创建/列表/详情、升版本、active 互斥、60s 缓存失效、`/test`（OCR 注入）。
"""
from __future__ import annotations

import io
import secrets
from collections.abc import Iterator

import pytest
from alembic import command
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.field_rules import FieldRule
from app.models.ticket_templates import TicketTemplate
from app.models.users import User
from app.ocr.types import OcrLine
from app.schemas.template import TemplateCreate
from app.services import template_service
from app.services.template_registry import registry
from app.services.template_seed import build_seed_templates
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/templates"


# --------------------------------------------------------------------------- #
# 单元
# --------------------------------------------------------------------------- #
GOOD_SPECS = {
    "total_amount": {
        "strategy": "total_zone",
        "keywords": ["价税合计", "合计"],
        "direction": "right",
        "regex": r"\d{1,3}(,\d{3})*(\.\d{1,2})?",
        "max_distance": 400,
        "required": True,
        "value_type": "money",
    }
}


def _payload(**overrides) -> dict:
    body = {
        "type_code": "vat_digital",
        "type_name": "演示票种",
        "match_keywords": ["演示票", "测试发票"],
        "field_specs_json": GOOD_SPECS,
        "active": True,
    }
    body.update(overrides)
    return body


def test_template_create_accepts_valid_payload() -> None:
    model = TemplateCreate(**_payload())
    assert model.type_code == "vat_digital"
    assert model.match_keywords == ["演示票", "测试发票"]


@pytest.mark.parametrize(
    ("overrides",),
    [
        ({"type_code": "Bad Code"},),
        ({"match_keywords": []},),
        ({"match_keywords": ["ok", "  "]},),
        ({"field_specs_json": {}},),
        (
            {
                "field_specs_json": {
                    "total_amount": {"keywords": ["合计"], "regex": "(["}
                }
            },
        ),
        (
            {
                "field_specs_json": {
                    "total_amount": {"keywords": ["合计"], "strategy": "bogus"}
                }
            },
        ),
        ({"rules": [{"field_name": "a"}, {"field_name": "a"}]},),
    ],
)
def test_template_create_rejects_invalid_payload(overrides) -> None:
    with pytest.raises(ValueError):
        TemplateCreate(**_payload(**overrides))


def test_seed_builder_covers_vat_digital_and_limits_keywords() -> None:
    seeds = build_seed_templates()
    type_codes = {seed.type_code for seed in seeds}
    assert "vat_digital" in type_codes
    assert {"vat_general", "train_ticket", "flight_itinerary", "taxi_ticket"} <= type_codes
    for seed in seeds:
        assert 1 <= len(seed.match_keywords) <= 10
        assert seed.field_specs_json
    vat_general = next(seed for seed in seeds if seed.type_code == "vat_general")
    assert "total_amount" in vat_general.field_specs_json
    assert vat_general.field_specs_json["total_amount"]["keywords"]


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def template_database() -> Iterator[URL]:
    with temporary_database("study_ocr_tpl_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(template_database: URL):
    eng = create_engine(
        template_database.render_as_string(hide_password=False), poolclass=NullPool
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
                "TRUNCATE users, ticket_templates, field_rules RESTART IDENTITY CASCADE"
            )
        )
    registry.invalidate()
    yield


@pytest.fixture()
def actors(session_factory, clean_db) -> dict[str, User]:
    with session_factory() as db:
        users = {
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "alice": User(username="alice", real_name="A", role="submitter", status="active"),
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


def _jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# 权限与 CRUD
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_permissions(client, actors) -> None:
    assert client.get(BASE, headers=_auth(actors["rita"])).status_code == 200
    assert client.get(BASE, headers=_auth(actors["alice"])).status_code == 403
    assert (
        client.post(BASE, json=_payload(), headers=_auth(actors["rita"])).status_code == 403
    )


@pytest.mark.integration
def test_create_list_detail(client, actors) -> None:
    created = client.post(BASE, json=_payload(), headers=_auth(actors["ada"]))
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert (data["version"], data["active"]) == (1, True)
    template_id = data["id"]

    listed = client.get(BASE, headers=_auth(actors["rita"]))
    items = listed.json()["data"]["items"]
    assert [item["id"] for item in items] == [template_id]
    assert "field_specs_json" not in items[0]

    detail = client.get(f"{BASE}/{template_id}", headers=_auth(actors["rita"]))
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert "total_amount" in detail_data["template"]["field_specs_json"]
    assert detail_data["rules"] == []

    duplicate = client.post(BASE, json=_payload(), headers=_auth(actors["ada"]))
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == 40900


@pytest.mark.integration
def test_list_filters_active_and_type(client, actors, session_factory) -> None:
    first = client.post(BASE, json=_payload(type_code="vat_digital"), headers=_auth(actors["ada"]))
    second = client.post(
        BASE, json=_payload(type_code="vat_general", active=False), headers=_auth(actors["ada"])
    )
    assert first.status_code == second.status_code == 201

    active_items = client.get(BASE, headers=_auth(actors["ada"])).json()["data"]["items"]
    assert {item["type_code"] for item in active_items} == {"vat_digital"}

    inactive = client.get(f"{BASE}?active=false", headers=_auth(actors["ada"]))
    assert {item["type_code"] for item in inactive.json()["data"]["items"]} == {"vat_general"}

    filtered = client.get(f"{BASE}?active=false&type_code=vat_general", headers=_auth(actors["ada"]))
    assert filtered.json()["data"]["total"] == 1


@pytest.mark.integration
def test_update_creates_new_version_and_copies_rules(client, actors, session_factory) -> None:
    payload = _payload(
        rules=[
            {
                "field_name": "total_amount",
                "pattern": r"\d+(\.\d{2})?",
                "validator": "amount",
                "error_msg": "金额格式",
                "priority": 10,
            }
        ]
    )
    created = client.post(BASE, json=payload, headers=_auth(actors["ada"])).json()["data"]
    old_id = created["id"]

    updated_payload = _payload(
        version=1,
        rules=[
            {
                "field_name": "total_amount",
                "pattern": r"\d+\.\d{2}",
                "validator": "amount",
                "error_msg": "金额格式 v2",
                "priority": 5,
            },
            {
                "field_name": "check_code",
                "pattern": r"\d{6}",
                "validator": "code_no",
                "priority": 20,
            },
        ],
    )
    updated = client.put(f"{BASE}/{old_id}", json=updated_payload, headers=_auth(actors["ada"]))
    assert updated.status_code == 200, updated.text
    new = updated.json()["data"]
    assert (new["version"], new["active"]) == (2, True)
    assert new["id"] != old_id

    old = client.get(f"{BASE}/{old_id}", headers=_auth(actors["ada"])).json()["data"]
    assert old["template"]["active"] is False
    assert len(old["rules"]) == 1

    new_detail = client.get(f"{BASE}/{new['id']}", headers=_auth(actors["ada"])).json()["data"]
    rules = {rule["field_name"]: rule for rule in new_detail["rules"]}
    assert set(rules) == {"total_amount", "check_code"}
    assert rules["total_amount"]["priority"] == 5
    assert rules["total_amount"]["error_msg"] == "金额格式 v2"

    stale = client.put(f"{BASE}/{old_id}", json=updated_payload, headers=_auth(actors["ada"]))
    assert stale.status_code == 409
    assert stale.json()["data"]["current_version"] == 2


@pytest.mark.integration
def test_only_one_active_version(client, actors, session_factory) -> None:
    created = client.post(BASE, json=_payload(), headers=_auth(actors["ada"])).json()["data"]
    client.put(
        f"{BASE}/{created['id']}",
        json=_payload(version=1, active=True),
        headers=_auth(actors["ada"]),
    )
    with session_factory() as db:
        actives = db.scalars(
            select(TicketTemplate).where(TicketTemplate.active.is_(True))
        ).all()
        assert len(actives) == 1
        assert actives[0].version == 2


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_registry_cache_ttl_and_invalidate(actors, session_factory) -> None:
    with session_factory() as db:
        template = TicketTemplate(
            type_code="train_ticket",
            type_name="缓存前",
            match_keywords=["缓存"],
            field_specs_json=GOOD_SPECS,
            version=1,
            active=True,
        )
        db.add(template)
        db.commit()
        template_id = template.id

    registry.invalidate()
    with session_factory() as db:
        assert registry.get(db, "train_ticket").type_name == "缓存前"

    # 直接改库、不 invalidate：60s 内仍是旧快照
    with session_factory() as db:
        db.execute(
            sa_text("update ticket_templates set type_name='缓存后' where id = :id"),
            {"id": template_id},
        )
        db.commit()
    with session_factory() as db:
        assert registry.get(db, "train_ticket").type_name == "缓存前"

    registry.invalidate()
    with session_factory() as db:
        assert registry.get(db, "train_ticket").type_name == "缓存后"


# --------------------------------------------------------------------------- #
# /test
# --------------------------------------------------------------------------- #
FAKE_LINE = OcrLine(
    text="价税合计 ¥113.00",
    score=0.98,
    box=[[10, 10], [400, 10], [400, 40], [10, 40]],
    line_no=0,
    y_center=25,
    x_left=10,
    height=30,
)


def _monkeypatch_ocr(monkeypatch, lines=None, exc: BaseException | None = None):
    def _fake(data: bytes):
        if exc is not None:
            raise exc
        return (lines or [FAKE_LINE]), (800, 600)

    monkeypatch.setattr(template_service, "_recognize_image", _fake)


@pytest.mark.integration
def test_template_test_endpoint_extracts_fields(
    client, actors, monkeypatch, session_factory
) -> None:
    payload = _payload(match_keywords=["价税合计"], rules=[{"field_name": "total_amount"}])
    created = client.post(BASE, json=payload, headers=_auth(actors["ada"])).json()["data"]
    _monkeypatch_ocr(monkeypatch)

    response = client.post(
        f"{BASE}/{created['id']}/test",
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
        headers=_auth(actors["ada"]),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["template_id"] == created["id"]
    assert data["type_code"] == "vat_digital"
    assert "价税合计" in data["matched_keywords"]
    assert data["overall_confidence"] > 0
    assert data["elapsed_ms"] >= 0
    fields = {item["field_name"]: item for item in data["fields"]}
    assert fields["total_amount"]["value"] == "113.00"
    assert fields["total_amount"]["bbox"]

    # 不建票、不写文件
    with session_factory() as db:
        assert db.scalar(select(sa_text("count(*) from invoices"))) == 0
        assert db.scalar(select(sa_text("count(*) from invoice_files"))) == 0


@pytest.mark.integration
def test_template_test_permissions_and_errors(client, actors, monkeypatch) -> None:
    created = client.post(BASE, json=_payload(), headers=_auth(actors["ada"])).json()["data"]
    _monkeypatch_ocr(monkeypatch)
    body = {"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")}

    assert client.post(f"{BASE}/{created['id']}/test", files=body, headers=_auth(actors["rita"])).status_code == 403

    fake = client.post(
        f"{BASE}/{created['id']}/test",
        files={"file": ("a.jpg", b"not an image", "image/jpeg")},
        headers=_auth(actors["ada"]),
    )
    assert fake.status_code == 422

    # 未启用模板不能试跑
    inactive = client.post(
        BASE, json=_payload(type_code="taxi_ticket", active=False), headers=_auth(actors["ada"])
    ).json()["data"]
    not_active = client.post(
        f"{BASE}/{inactive['id']}/test", files=body, headers=_auth(actors["ada"])
    )
    assert not_active.status_code == 422


@pytest.mark.integration
def test_template_test_ocr_failure_returns_50010(client, actors, monkeypatch) -> None:
    created = client.post(BASE, json=_payload(), headers=_auth(actors["ada"])).json()["data"]
    _monkeypatch_ocr(monkeypatch, exc=RuntimeError("engine down"))
    response = client.post(
        f"{BASE}/{created['id']}/test",
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
        headers=_auth(actors["ada"]),
    )
    assert response.status_code == 500
    assert response.json()["code"] == 50010