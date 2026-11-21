"""审计日志哈希链（S4-7）：追加写 + 全链校验。

设计（`docs/spec/s4-7-audit-chain.md`，D-081）：

```
entry_hash = HMAC-SM3(audit_key, canonical_json({
    prev_hash, ts, actor_id, actor_role, action,
    target_type, target_id, payload_sha256,
}))
payload_sha256 = SM3(canonical_json(detail_json or {}))
创世 prev_hash = 64 个 "0"
```

- **只 INSERT，不 UPDATE / DELETE**；`append_audit` 不 commit，由调用方与业务变更同一事务提交；
- 写入串行：同一事务里先 `pg_advisory_xact_lock(ADVISORY_LOCK_KEY)` 再取链尾（D-015）；
- `ts` 由 Python 显式给 UTC（哈希里是 `ISO-8601 + 微秒 + +00:00`），不依赖 DB 时钟；
- `payload_sha256` 不落列（SCHEMA `audit_logs` 没有该列）：校验时从 `detail_json` 重算，
  所以**改 detail 也会让 entry_hash 对不上**；
- 哈希载荷比 SCHEMA 的文字公式**多绑 `actor_role` 与 `target_type`**：不改这两列就无法"改一行不被发现"；
- 能力边界：防"改了不被发现"，不防"被读"，也不防"同时持有审计 key 的人整体重写"——不是区块链。

本卡只提供链内核与校验；各业务动作的审计行随 S5 接入（遗留 #21/#27/#54/#61 已重定向）。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ValidationFailedError
from app.crypto.canonical import CanonicalJSONError, canonical_json
from app.crypto.sm.sm3 import hmac_sm3, sm3_hex
from app.models.audit_logs import AuditLog
from app.models.enums import AuditAction, AuditActorRole, AuditTargetType
from app.schemas.audit import AuditListQuery, AuditLogOut
from app.schemas.common import PageResult
from app.services.pagination import paginate

logger = logging.getLogger("app.audit")

#: 创世行的 prev_hash（SCHEMA：64 个 0）
GENESIS_HASH: Final = "0" * 64
#: 审计链串行写入的咨询锁 key（D-015 的固定常量；同一库所有 writer 共用）
ADVISORY_LOCK_KEY: Final = 8071701

REASON_PREV_MISMATCH: Final = "prev_hash_mismatch"
REASON_ENTRY_MISMATCH: Final = "entry_hash_mismatch"
REASON_PAYLOAD_INVALID: Final = "payload_invalid"

_ACTIONS: Final = frozenset(item.value for item in AuditAction)
_ACTOR_ROLES: Final = frozenset(item.value for item in AuditActorRole)
_TARGET_TYPES: Final = frozenset(item.value for item in AuditTargetType)

SORT_MAP = {"created_at": AuditLog.created_at, "id": AuditLog.id}


@dataclass(frozen=True)
class ChainVerification:
    """全链校验结论（对应 API.md §7 的 `verify-chain` 出参）。"""

    ok: bool
    checked_count: int
    broken_at: int | None = None  # 首个断点的行序号（从 1 起）
    broken_id: int | None = None
    reason: str | None = None


def payload_sha256(detail: Mapping[str, Any] | None) -> str:
    """`detail_json` 的规范化摘要。`None` 与 `{}` 等价（统一按空对象算）。

    `detail` 里禁止 float（金额先格式化成字符串），非 str 键同样拒绝——沿用 S4-1 的 canonical 口径。
    """
    if detail is None:
        payload: dict[str, Any] = {}
    elif isinstance(detail, Mapping):
        payload = dict(detail)
    else:
        raise TypeError(f"detail 必须是 dict 或 None，收到 {type(detail).__name__}")
    try:
        return sm3_hex(canonical_json(payload))
    except CanonicalJSONError as exc:
        raise CanonicalJSONError(f"审计 detail 不合法：{exc}") from exc


def chain_hash(
    *,
    prev_hash: str,
    created_at: datetime,
    actor_id: int | None,
    actor_role: str | None,
    action: str,
    target_type: str | None,
    target_id: int | None,
    payload_sha256: str,
    key: bytes | str | None = None,
) -> str:
    """单条审计行的 HMAC-SM3（64 位小写 hex）。"""
    return hmac_sm3(
        _key_bytes(key),
        _chain_payload(
            prev_hash=prev_hash,
            created_at=created_at,
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload_sha256=payload_sha256,
        ),
    ).hex()


def append_audit(
    db: Session,
    *,
    action: str,
    trace_id: str,
    actor_id: int | None = None,
    actor_role: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    key: bytes | str | None = None,
) -> AuditLog:
    """追加一条审计行（不 commit）。

    调用方必须在**同一事务**里先做业务变更、再调本函数、最后一起 commit；
    `pg_advisory_xact_lock` 会一直持有到事务结束，保证并发写不分叉。
    """
    if not isinstance(trace_id, str) or not trace_id.strip():
        raise ValueError("trace_id 不能为空")
    action_value = _require_enum(action, _ACTIONS, "action")
    actor_role_value = _require_enum(actor_role, _ACTOR_ROLES, "actor_role")
    target_type_value = _require_enum(target_type, _TARGET_TYPES, "target_type")
    timestamp = _as_utc(now)
    detail_hash = payload_sha256(detail)
    secret = _key_bytes(key)

    # 串行追加：先锁链尾再读上一条，事务结束自动释放（D-015）。
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": ADVISORY_LOCK_KEY})
    prev_hash = (
        db.scalar(select(AuditLog.entry_hash).order_by(AuditLog.id.desc()).limit(1))
        or GENESIS_HASH
    )

    row = AuditLog(
        trace_id=trace_id,
        actor_id=actor_id,
        actor_role=actor_role_value,
        action=action_value,
        target_type=target_type_value,
        target_id=target_id,
        detail_json=dict(detail) if detail is not None else None,
        prev_hash=prev_hash,
        entry_hash=chain_hash(
            prev_hash=prev_hash,
            created_at=timestamp,
            actor_id=actor_id,
            actor_role=actor_role_value,
            action=action_value,
            target_type=target_type_value,
            target_id=target_id,
            payload_sha256=detail_hash,
            key=secret,
        ),
        created_at=timestamp,
    )
    db.add(row)
    db.flush()
    return row


def verify_chain(db: Session, *, key: bytes | str | None = None) -> ChainVerification:
    """按 `id` 升序全量复算；返回首个断点（不抛异常，便于接口原样展示）。"""
    secret = _key_bytes(key)
    expected_prev = GENESIS_HASH
    checked = 0

    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.asc())).yield_per(500)
    for row in rows:
        checked += 1
        if row.prev_hash != expected_prev:
            return ChainVerification(
                ok=False,
                checked_count=checked,
                broken_at=checked,
                broken_id=row.id,
                reason=REASON_PREV_MISMATCH,
            )
        try:
            recomputed = chain_hash(
                prev_hash=row.prev_hash,
                created_at=row.created_at,
                actor_id=row.actor_id,
                actor_role=row.actor_role,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                payload_sha256=payload_sha256(row.detail_json),
                key=secret,
            )
        except (CanonicalJSONError, TypeError, ValueError):
            return ChainVerification(
                ok=False,
                checked_count=checked,
                broken_at=checked,
                broken_id=row.id,
                reason=REASON_PAYLOAD_INVALID,
            )
        if recomputed != row.entry_hash:
            return ChainVerification(
                ok=False,
                checked_count=checked,
                broken_at=checked,
                broken_id=row.id,
                reason=REASON_ENTRY_MISMATCH,
            )
        expected_prev = row.entry_hash

    return ChainVerification(ok=True, checked_count=checked)


def list_audit_logs(db: Session, query: AuditListQuery) -> PageResult[AuditLogOut]:
    """审计列表（admin）：筛选 + 分页 + 白名单排序。"""
    stmt = select(AuditLog)
    if query.actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == query.actor_id)
    if query.action is not None:
        stmt = stmt.where(AuditLog.action == query.action)
    if query.target_type is not None:
        stmt = stmt.where(AuditLog.target_type == query.target_type)
    if query.target_id is not None:
        stmt = stmt.where(AuditLog.target_id == query.target_id)
    if query.trace_id is not None:
        stmt = stmt.where(AuditLog.trace_id == query.trace_id)

    date_from = _optional_utc(query.date_from, "from")
    date_to = _optional_utc(query.date_to, "to")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValidationFailedError("from 不能晚于 to")
    if date_from is not None:
        stmt = stmt.where(AuditLog.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(AuditLog.created_at <= date_to)

    page = paginate(db, stmt, AuditLog, SORT_MAP, query, default="created_at")
    return PageResult[AuditLogOut](
        items=[AuditLogOut.model_validate(row) for row in page.items],
        total=page.total,
        page=page.page,
        page_size=page.page_size,
    )


# ---------------------------------------------------------------- 内部


def _chain_payload(
    *,
    prev_hash: str,
    created_at: datetime,
    actor_id: int | None,
    actor_role: str | None,
    action: str,
    target_type: str | None,
    target_id: int | None,
    payload_sha256: str,
) -> bytes:
    return canonical_json(
        {
            "prev_hash": prev_hash,
            "ts": _format_ts(created_at),
            "actor_id": actor_id,
            "actor_role": actor_role,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "payload_sha256": payload_sha256,
        }
    )


def _format_ts(value: datetime) -> str:
    """哈希里固定用 UTC + 微秒的 ISO-8601，写读两侧格式一致。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at 必须带时区（统一 UTC）")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now 必须带时区（统一 UTC）")
    return value.astimezone(timezone.utc)


def _optional_utc(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailedError(f"{name} 必须是带时区的 ISO-8601 时间")
    return value.astimezone(timezone.utc)


def _require_enum(value: str | None, allowed: frozenset[str], name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{name} 非法：{value!r}（可选 {sorted(allowed)}）")
    return value


def _key_bytes(key: bytes | str | None) -> bytes:
    if key is None:
        key = get_settings().audit_hmac_key
    if isinstance(key, str):
        return key.encode("utf-8")
    if isinstance(key, (bytes, bytearray)):
        return bytes(key)
    raise TypeError(f"审计 key 必须是 str 或 bytes，收到 {type(key).__name__}")