"""签名记录与"规范化 → 摘要 → 签名 / 验签 → 归类"链路。

记录的字段对齐 SCHEMA `signatures`（`algo` / `digest_algo` / `digest_hex` / `signature_b64` /
`cert_id`）与任务卡的 `canonical_payload_base64` / `payload_version`：

- `digest_hex` = `SM3(canonical_bytes)`：按 SCHEMA 只用于**展示与快速比对，不是验签依据**；
  真正的验签依据是 `signature_b64`（SM2-SM3，已含 ZA 绑定）与记录里的原始载荷；
- `canonical_payload_base64` = 签名当时的规范化字节（AGENTS §10.4：验签时用它重算，
  **不重新拼装**），免得 later 代码改了 canonical 规则导致历史签名集体失效；
- 验签结论按 SCHEMA `verify_result` 的取值归类：`ok` / `digest_mismatch` / `bad_signature`；
  `cert_invalid` 属 S4-6 证书层，本卡不产生。
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.crypto.canonical import canonical_json
from app.crypto.sm.sm2 import sign_bytes, verify_bytes
from app.crypto.sm.sm3 import sm3_digest

__all__ = [
    "SignatureRecord",
    "VerifyResult",
    "sign_canonical",
    "verify_canonical",
    "verify_record",
]


class VerifyResult(StrEnum):
    """验签结论（与 SCHEMA `signatures.verify_result` 的 CHECK 取值对齐）。"""

    OK = "ok"
    DIGEST_MISMATCH = "digest_mismatch"
    BAD_SIGNATURE = "bad_signature"


class SignatureRecord(BaseModel):
    """一次签名的不可变记录（S5 直接映射到 `signatures` 行）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    algo: Literal["SM2-SM3"] = "SM2-SM3"
    digest_algo: Literal["SM3"] = "SM3"
    digest_hex: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_b64: str = Field(min_length=1)
    canonical_payload_base64: str = Field(min_length=1)
    payload_version: str = Field(default="v1", min_length=1, max_length=32)
    # S4-6 之前允许为空；S5 落库前必须补齐（SCHEMA 该列 NOT NULL）。
    cert_id: int | None = Field(default=None, ge=1)
    signed_at: datetime

    @field_validator("signed_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("signed_at 必须带时区（统一用 UTC）")
        return value


def sign_canonical(
    obj: object,
    *,
    private_key_hex: str,
    public_key_hex: str,
    cert_id: int | None = None,
    payload_version: str = "v1",
    now: datetime | None = None,
) -> SignatureRecord:
    """把业务对象规范化后签名，产出可直接落库的记录。"""
    canonical = canonical_json(obj)
    signature_hex = sign_bytes(canonical, private_key_hex=private_key_hex, public_key_hex=public_key_hex)
    return SignatureRecord(
        digest_hex=sm3_digest(canonical).hex(),
        signature_b64=base64.b64encode(bytes.fromhex(signature_hex)).decode("ascii"),
        canonical_payload_base64=base64.b64encode(canonical).decode("ascii"),
        payload_version=payload_version,
        cert_id=cert_id,
        signed_at=now or datetime.now(timezone.utc),
    )


def verify_record(record: SignatureRecord, *, public_key_hex: str) -> VerifyResult:
    """只验记录本身（不比对业务对象）。载荷改过 → digest_mismatch；签名不符 → bad_signature。"""
    payload = _decode_b64(record.canonical_payload_base64)
    if payload is None:
        return VerifyResult.DIGEST_MISMATCH
    if sm3_digest(payload).hex() != record.digest_hex:
        return VerifyResult.DIGEST_MISMATCH

    signature = _decode_b64(record.signature_b64)
    if signature is None:
        return VerifyResult.BAD_SIGNATURE
    if verify_bytes(payload, signature.hex(), public_key_hex=public_key_hex):
        return VerifyResult.OK
    return VerifyResult.BAD_SIGNATURE


def verify_canonical(obj: object, record: SignatureRecord, *, public_key_hex: str) -> VerifyResult:
    """连同业务对象一起验：对象的规范化字节必须与记录里的载荷逐字节相同。"""
    canonical = canonical_json(obj)
    if base64.b64encode(canonical).decode("ascii") != record.canonical_payload_base64:
        return VerifyResult.DIGEST_MISMATCH
    return verify_record(record, public_key_hex=public_key_hex)


def _decode_b64(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None