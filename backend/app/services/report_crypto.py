"""签名与提交共用的密码编排（S5-2）：签名域 / 封包明文 / `EnvelopeV1`。

为什么在 service 而不是 `crypto/`：
- 这里做的是**业务编排**（拼哪些字段、哪个对象进哪层信封），算法本身仍全部在 `crypto/` 与
  `provider.py` 后面；`crypto/envelope.py`（S4-4）是国密参考实现，业务路径按 provider 走 `EnvelopeV1`；
- 签名对象永远是 `canonical_json(...)` 的字节（AGENTS §10.4），本模块是唯一的拼装入口，
  S5-4 验签时复用同一个函数/同一份库里存的字节。

`EnvelopeV1` 的关联数据（AAD）把 `wrapped_key` 与 `payload_sha256` 都绑进去：
SM4 的 MAC 覆盖 `aad‖IV‖密文`、AES-GCM 的 tag 覆盖 `aad‖密文`，于是"单改包裹密钥"或
"换摘要"都会先被完整性校验拦下（与 S4-4 把 wrapped_key 进 MAC 的思路一致，D-077/D-085）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.crypto.canonical import canonical_json
from app.crypto.provider import AlgoIntegrityError, AlgoProvider, KeyMaterial, SymmetricPayload
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.report_items import ReportItem
from app.models.signatures import Signature

__all__ = [
    "AlgoNames",
    "EnvelopeOpenError",
    "SealedOpen",
    "SealedPayload",
    "SubmitItem",
    "algo_names",
    "build_sealed_report",
    "build_signing_payload",
    "canonical_signing_bytes",
    "envelope_aad",
    "open_sealed",
    "open_sealed_steps",
    "rebuild_signing_canonical",
    "seal_payload",
]

PAYLOAD_VERSION = "v1"
SEALED_PAYLOAD_VERSION = "report-v1"
ENVELOPE_VERSION = "envelope-v1"


class EnvelopeOpenError(Exception):
    """拆封失败；`reason` 与 S4 的口径对齐（`mac_mismatch` / `digest_mismatch` / …）。

    `steps` 记录失败前已通过的分步（S5-4 的 7 步口径），供接口原样展示。
    """

    def __init__(self, reason: str, steps: dict[str, bool] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.steps = dict(steps or {})


@dataclass(frozen=True)
class AlgoNames:
    """provider 对应的落库枚举值（SCHEMA §9.16 / §9.17）。"""

    signature_algo: str
    cert_algo: str
    envelope_key_algo: str
    cipher_algo: str


@dataclass(frozen=True)
class SubmitItem:
    """一条明细 + 票据 + 文件（service 一次查好，避免 builder 里再查库）。"""

    item: ReportItem
    invoice: Invoice
    file: InvoiceFile


@dataclass(frozen=True)
class SealedPayload:
    key_algo: str
    cipher_algo: str
    iv_b64: str
    mac_b64: str
    wrapped_key_b64: str
    ciphertext: bytes
    payload_sha256: str


def algo_names(provider: AlgoProvider) -> AlgoNames:
    if provider.name == "sm":
        return AlgoNames("SM2", "SM2", "SM2", "SM4-CBC-HMAC-SM3")
    if provider.name == "rsa":
        return AlgoNames("RSA-PSS", "RSA", "RSA-OAEP", "AES-256-GCM")
    raise ValueError(f"未知 provider：{provider.name!r}")


# --------------------------------------------------------------------------- #
# 签名域（payload v1）
# --------------------------------------------------------------------------- #
def build_signing_payload(
    form: ReportForm, items: list[SubmitItem], *, nonce: str
) -> dict[str, Any]:
    """拼签名域：单据业务字段 + 每张票 `(code,no,amount,file_sha256)` + nonce + created_at。

    金额一律字符串（canonical 拒 float/Decimal）；items 按 `invoice_id` 升序，保证确定性。
    """
    rows = sorted(items, key=lambda row: row.item.invoice_id)
    return {
        "payload_version": PAYLOAD_VERSION,
        "report_no": form.report_no,
        "applicant_id": form.applicant_id,
        "title": form.title,
        "reason": form.reason,
        "total_amount": _money(form.total_amount),
        "item_count": form.item_count,
        "created_at": _iso(form.created_at),
        "nonce": nonce,
        "items": [
            {
                "invoice_code": row.invoice.invoice_code,
                "invoice_no": row.invoice.invoice_no,
                "amount": _money(row.item.amount),
                "file_sha256": row.file.sha256,
            }
            for row in rows
        ],
    }


def canonical_signing_bytes(form: ReportForm, items: list[SubmitItem], *, nonce: str) -> bytes:
    return canonical_json(build_signing_payload(form, items, nonce=nonce))


# --------------------------------------------------------------------------- #
# 封包明文（report-v1）
# --------------------------------------------------------------------------- #
def build_sealed_report(
    form: ReportForm,
    items: list[SubmitItem],
    *,
    applicant_username: str,
    applicant_dept: str | None,
    joined_fields: dict[int, dict[str, str]],
    signature: Signature,
    signature_cert_fp: str,
    submitted_at: datetime,
) -> dict[str, Any]:
    """财务端拆封后要看到的那份数据：单据 + 明细 + 字段值 + 签名元数据。"""
    rows = sorted(items, key=lambda row: row.item.invoice_id)
    return {
        "payload_version": SEALED_PAYLOAD_VERSION,
        "report_no": form.report_no,
        "applicant": {
            "id": form.applicant_id,
            "username": applicant_username,
            "dept": applicant_dept,
        },
        "title": form.title,
        "reason": form.reason,
        "total_amount": _money(form.total_amount),
        "item_count": form.item_count,
        "created_at": _iso(form.created_at),
        "submitted_at": _iso(submitted_at),
        "items": [
            {
                "invoice_id": row.invoice.id,
                "invoice_code": row.invoice.invoice_code,
                "invoice_no": row.invoice.invoice_no,
                "invoice_date": _date(row.invoice.invoice_date),
                "buyer_name": row.invoice.buyer_name,
                "seller_name": row.invoice.seller_name,
                "amount": _money(row.invoice.amount),
                "tax_amount": _money(row.invoice.tax_amount),
                "total_amount": _money(row.invoice.total_amount),
                "file_sha256": row.file.sha256,
                "fields": joined_fields.get(row.invoice.id, {}),
            }
            for row in rows
        ],
        "signature": {
            "algo": signature.algo,
            "digest_hex": signature.digest_hex,
            "signature_b64": signature.signature_b64,
            "cert_fp": signature_cert_fp,
        },
    }


# --------------------------------------------------------------------------- #
# EnvelopeV1（provider 版）：封装 / 拆封
# --------------------------------------------------------------------------- #
def seal_payload(
    *,
    provider: AlgoProvider,
    recipient: KeyMaterial,
    recipient_cert_fp: str,
    plaintext: bytes,
    now: datetime | None = None,
) -> SealedPayload:
    names = algo_names(provider)
    cek = secrets.token_bytes(provider.symmetric_key_bytes)
    wrapped_key_b64 = _b64(provider.encrypt_key(cek, recipient))
    payload_sha256 = hashlib.sha256(plaintext).hexdigest()
    associated = envelope_aad(
        key_algo=names.envelope_key_algo,
        cipher_algo=names.cipher_algo,
        recipient_cert_fp=recipient_cert_fp,
        wrapped_key_b64=wrapped_key_b64,
        payload_sha256=payload_sha256,
    )
    sealed = provider.wrap_symmetric(cek, plaintext, aad=associated)
    return SealedPayload(
        key_algo=names.envelope_key_algo,
        cipher_algo=names.cipher_algo,
        iv_b64=_b64(sealed.nonce),
        mac_b64=_b64(sealed.tag),
        wrapped_key_b64=wrapped_key_b64,
        ciphertext=bytes(sealed.ciphertext),
        payload_sha256=payload_sha256,
    )


@dataclass(frozen=True)
class SealedOpen:
    """分步解封结果：`steps` 里只有实际走到的步骤为 True。"""

    plaintext: bytes
    steps: dict[str, bool]


def open_sealed_steps(
    *,
    provider: AlgoProvider,
    private_key: KeyMaterial,
    key_algo: str,
    cipher_algo: str,
    recipient_cert_fp: str,
    wrapped_key_b64: str,
    iv_b64: str,
    mac_b64: str,
    ciphertext: bytes,
    payload_sha256: str,
) -> SealedOpen:
    """解 CEK → 验 MAC/解 → 摘要比对，并把 `unwrap_cek/mac/decrypt` 的步进结果带出来。

    SM4 的 MAC 与解密在 `provider.unwrap_symmetric` 内部完成（先验 MAC 再解密，D-076），
    所以失败原因区分 `mac_mismatch`（MAC 步）与 `bad_padding`（解密步）。
    """
    steps = {"unwrap_cek": False, "mac": False, "decrypt": False}
    wrapped_key = _unb64(wrapped_key_b64)
    iv = _unb64(iv_b64)
    mac = _unb64(mac_b64)
    if wrapped_key is None or iv is None or mac is None:
        raise EnvelopeOpenError("bad_envelope", steps)

    try:
        cek = provider.decrypt_key(wrapped_key, private_key)
    except Exception as exc:  # noqa: BLE001 - 归一到拆封错误，不泄露细节
        raise EnvelopeOpenError("bad_key", steps) from exc
    steps["unwrap_cek"] = True

    associated = envelope_aad(
        key_algo=key_algo,
        cipher_algo=cipher_algo,
        recipient_cert_fp=recipient_cert_fp,
        wrapped_key_b64=wrapped_key_b64,
        payload_sha256=payload_sha256,
    )
    try:
        plaintext = provider.unwrap_symmetric(
            cek,
            SymmetricPayload(nonce=iv, ciphertext=bytes(ciphertext), tag=mac),
            aad=associated,
        )
    except AlgoIntegrityError as exc:
        if exc.reason != "mac_mismatch":
            # MAC 通过、解密/去填充失败
            steps["mac"] = True
        raise EnvelopeOpenError(exc.reason, steps) from exc
    steps["mac"] = True
    steps["decrypt"] = True

    if hashlib.sha256(plaintext).hexdigest() != payload_sha256:
        raise EnvelopeOpenError("digest_mismatch", steps)
    return SealedOpen(plaintext=plaintext, steps=steps)


def open_sealed(
    *,
    provider: AlgoProvider,
    private_key: KeyMaterial,
    key_algo: str,
    cipher_algo: str,
    recipient_cert_fp: str,
    wrapped_key_b64: str,
    iv_b64: str,
    mac_b64: str,
    ciphertext: bytes,
    payload_sha256: str,
) -> bytes:
    """S5-4 的解封原语：解 CEK → 验 MAC/解 → 摘要比对（不带分步）。"""
    return open_sealed_steps(
        provider=provider,
        private_key=private_key,
        key_algo=key_algo,
        cipher_algo=cipher_algo,
        recipient_cert_fp=recipient_cert_fp,
        wrapped_key_b64=wrapped_key_b64,
        iv_b64=iv_b64,
        mac_b64=mac_b64,
        ciphertext=ciphertext,
        payload_sha256=payload_sha256,
    ).plaintext


def rebuild_signing_canonical(
    form: ReportForm, items: list[SubmitItem], stored_canonical: str
) -> bytes:
    """**从库内当前数据**重建签名域字节：nonce 取自已存的 canonical（防重放/防重拼）。

    用途（S5-4 的 digest_recheck）：如果业务行被改过而签名域没改，重建结果会与库里那份不一致，
    从而检出"改了报价单但保留旧签名"的攻击；只比对 `signatures.digest_hex`（提交时存下的值）
    等于没验签，所以必须重建后再比对字节。
    """
    try:
        parsed = json.loads(stored_canonical)
    except ValueError as exc:
        raise ValueError("已存的 canonical 不是合法 JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("已存的 canonical 不是 JSON 对象")
    nonce = parsed.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        raise ValueError("已存的 canonical 缺少 nonce")
    return canonical_json(build_signing_payload(form, items, nonce=nonce))


def envelope_aad(
    *,
    key_algo: str,
    cipher_algo: str,
    recipient_cert_fp: str,
    wrapped_key_b64: str,
    payload_sha256: str,
) -> bytes:
    """AAD 只由 `envelopes` 行里的字段构成，拆封端可无损重建。"""
    return canonical_json(
        {
            "version": ENVELOPE_VERSION,
            "key_algo": key_algo,
            "cipher_algo": cipher_algo,
            "recipient_cert_fp": recipient_cert_fp,
            "wrapped_key_b64": wrapped_key_b64,
            "payload_sha256": payload_sha256,
        }
    )


# ---------------------------------------------------------------- 内部


def _money(value: object) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}"


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须带时区（统一 UTC）")
    return value.astimezone(timezone.utc).isoformat()


def _date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None