"""S4-7：审计链纯函数（不连库）。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.crypto.canonical import CanonicalJSONError, canonical_json
from app.crypto.sm.sm3 import sm3_hex
from app.services.audit import GENESIS_HASH, chain_hash, payload_sha256

NOW = datetime(2026, 11, 21, 8, 0, 0, 123456, tzinfo=timezone.utc)
KEY = "k" * 32

BASE = {
    "prev_hash": GENESIS_HASH,
    "created_at": NOW,
    "actor_id": 1,
    "actor_role": "submitter",
    "action": "upload",
    "target_type": "invoice",
    "target_id": 7,
    "payload_sha256": payload_sha256({"a": 1}),
}


# ---------------------------------------------------------------- payload_sha256


def test_payload_sha256_matches_canonical_sm3() -> None:
    assert payload_sha256({"a": 1}) == sm3_hex(canonical_json({"a": 1}))
    # 键序不影响（S4-1 的 canonical 口径）
    assert payload_sha256({"b": 2, "a": 1}) == payload_sha256({"a": 1, "b": 2})
    # None 与 {} 等价
    assert payload_sha256(None) == payload_sha256({})


@pytest.mark.parametrize(
    "detail",
    [{"amount": 1.5}, {1: "x"}],
    ids=["float", "non_str_key"],
)
def test_payload_sha256_rejects_non_canonical_detail(detail: dict) -> None:
    with pytest.raises(CanonicalJSONError):
        payload_sha256(detail)


def test_payload_sha256_rejects_non_mapping() -> None:
    with pytest.raises(TypeError):
        payload_sha256(["not", "a", "mapping"])  # type: ignore[arg-type]


# ---------------------------------------------------------------- chain_hash


def test_chain_hash_is_deterministic_hex() -> None:
    first = chain_hash(**BASE, key=KEY)
    second = chain_hash(**BASE, key=KEY)

    assert first == second
    assert len(first) == 64
    int(first, 16)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prev_hash", "f" * 64),
        ("created_at", datetime(2026, 11, 21, 8, 0, 1, tzinfo=timezone.utc)),
        ("actor_id", 2),
        ("actor_role", "reviewer"),
        ("action", "confirm_field"),
        ("target_type", "form"),
        ("target_id", 8),
        ("payload_sha256", "a" * 64),
    ],
)
def test_chain_hash_binds_every_field(field: str, value: object) -> None:
    changed = {**BASE, field: value}

    assert chain_hash(**changed, key=KEY) != chain_hash(**BASE, key=KEY)  # type: ignore[arg-type]


def test_chain_hash_depends_on_key() -> None:
    assert chain_hash(**BASE, key="a" * 32) != chain_hash(**BASE, key="b" * 32)


def test_chain_hash_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError):
        chain_hash(**{**BASE, "created_at": datetime(2026, 1, 1, 0, 0, 0)}, key=KEY)


def test_genesis_hash_is_64_zeros() -> None:
    assert GENESIS_HASH == "0" * 64


# ---------------------------------------------------------------- append_audit 的入参校验（不碰库）


def test_append_audit_validates_arguments_before_db() -> None:
    from app.services.audit import append_audit

    with pytest.raises(ValueError):
        append_audit(None, action="not_an_action", trace_id="t1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        append_audit(None, action="upload", trace_id="  ")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        append_audit(None, action="upload", trace_id="t1", target_type="nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        append_audit(None, action="upload", trace_id="t1", actor_role="root")  # type: ignore[arg-type]
    with pytest.raises(CanonicalJSONError):
        append_audit(None, action="upload", trace_id="t1", detail={"amount": 1.5})  # type: ignore[arg-type]


# ---------------------------------------------------------------- 每日校验任务


async def test_anchor_audit_task_reports_chain_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """锚定任务只复算 + 记日志，不写审计行；结果字段与 API 一致。"""
    from contextlib import contextmanager

    from app.services import audit as audit_module
    from app.tasks import ocr_task as task_module

    from app.services.audit import ChainVerification

    @contextmanager
    def _fake_scope():
        yield object()

    monkeypatch.setattr("app.core.database.session_scope", _fake_scope)
    monkeypatch.setattr(
        audit_module,
        "verify_chain",
        lambda db: ChainVerification(ok=True, checked_count=42),
    )

    result = await task_module.anchor_audit_task({})

    assert result == {
        "anchored": True,
        "checked_count": 42,
        "broken_at": None,
        "broken_id": None,
        "reason": None,
    }