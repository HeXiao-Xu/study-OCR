"""S3-6 规则来源：DB active 模板优先、库空回退 YAML；流水线与 confirm 必须同源。"""
from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models.field_rules import FieldRule
from app.models.ticket_templates import TicketTemplate
from app.services import rule_source, template_registry
from app.services.template_seed import build_seed_templates
from tests.db_helpers import alembic_config, temporary_database

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def rule_database() -> URL:
    with temporary_database("study_ocr_rules_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def session_factory(rule_database: URL):
    engine = create_engine(rule_database.render_as_string(hide_password=False), poolclass=NullPool)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.fixture()
def clean_db(session_factory):
    with session_factory() as db:
        db.execute(sa_text("TRUNCATE users RESTART IDENTITY CASCADE"))
        db.commit()


def test_falls_back_to_yaml_when_no_template(session_factory, clean_db) -> None:
    template_registry.registry.invalidate()
    with session_factory() as db:
        resolved = rule_source.resolve_rules(db, "vat_digital")

    assert resolved.source == "yaml"
    assert resolved.template_id is None
    assert {rule.name for rule in resolved.rules} >= {"invoice_no", "total_amount"}


def test_uses_active_db_template(session_factory, clean_db) -> None:
    seed = next(item for item in build_seed_templates() if item.type_code == "vat_digital")
    with session_factory() as db:
        template = TicketTemplate(
            type_code=seed.type_code, type_name=seed.type_name,
            match_keywords=seed.match_keywords, field_specs_json=seed.field_specs_json,
            version=1, active=True,
        )
        db.add(template)
        db.flush()
        for rule in seed.rules:
            db.add(FieldRule(template_id=template.id, field_name=rule.field_name,
                             pattern=rule.pattern, validator=rule.validator,
                             error_msg=rule.error_msg, priority=rule.priority))
        db.commit()
        template_id = template.id

    template_registry.registry.invalidate()
    with session_factory() as db:
        resolved = rule_source.resolve_rules(db, "vat_digital")

    assert resolved.source == "db"
    assert resolved.template_id == template_id
    assert {rule.name for rule in resolved.rules} == set(seed.field_specs_json)