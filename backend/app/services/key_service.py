"""证书/密钥的选取、路径解析与解锁（S5-2）。

分工：
- `crypto/key_loader.py`：只管"文件 → KeyMaterial / 证书 → 公钥材料"；
- 本模块：选哪一条 `key_records` / `certificates` 行、路径怎么解析、解锁与证书校验失败怎么报错。

三条纪律：
- `storage_path` **只登记路径**，私钥本体永远不读进数据库（SCHEMA §2.3）；
- 路径解析有根：`KEY_STORAGE_ROOT`（空 = 仓库根），相对路径不许逃出根；
- 证书/密钥不可用一律 `409/40900`，口令问题一律 `422/42200` **统一文案**（不区分原因，防 oracle）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import ConflictError, ValidationFailedError
from app.crypto.key_loader import (
    KeyLoadError,
    certificate_problem,
    load_key_material,
    recipient_key_material,
)
from app.crypto.provider import AlgoProvider, KeyMaterial, get_provider
from app.models.certificates import Certificate
from app.models.enums import CertOwnerType, KeyType
from app.models.key_records import KeyRecord
from app.models.users import User
from app.services.report_crypto import algo_names

__all__ = [
    "SigningIdentity",
    "get_finance_recipient",
    "get_signing_identity",
    "load_signing_key",
    "provider_for",
    "resolve_storage_path",
]

#: `backend/app/services/key_service.py` → 仓库根（`KEY_STORAGE_ROOT` 为空时的解析根）
_REPO_ROOT = Path(__file__).resolve().parents[3]
_UNIFORM_KEY_ERROR = "私钥口令错误或密钥不可用"


@dataclass(frozen=True)
class SigningIdentity:
    key_record: KeyRecord
    certificate: Certificate


def provider_for(settings: Settings) -> AlgoProvider:
    return get_provider(settings.algo_provider)


def storage_root(settings: Settings) -> Path:
    raw = (settings.key_storage_root or "").strip()
    return Path(raw).expanduser().resolve() if raw else _REPO_ROOT


def resolve_storage_path(storage_path: str, settings: Settings) -> Path:
    """把 `key_records.storage_path` 解析成真实路径。

    绝对路径原样使用（运维可在 `.env` 指向外部密钥仓）；相对路径按 `KEY_STORAGE_ROOT` 解析，
    且解析结果必须仍在根内——防 `../` 把加载器带出密钥目录。
    """
    raw = Path(storage_path)
    if raw.is_absolute():
        return raw
    root = storage_root(settings)
    resolved = (root / raw).resolve()
    if not resolved.is_relative_to(root):
        raise ConflictError("密钥路径非法（超出 KEY_STORAGE_ROOT）")
    return resolved


def get_signing_identity(
    db: Session, user: User, provider: AlgoProvider
) -> SigningIdentity:
    """取本人最新的有效签名密钥与证书；算法必须与当前 provider 一致。"""
    expected_algo = algo_names(provider).cert_algo
    record = db.scalars(
        select(KeyRecord)
        .where(
            KeyRecord.user_id == user.id,
            KeyRecord.key_type == KeyType.SIGN.value,
            KeyRecord.revoked_at.is_(None),
        )
        .order_by(KeyRecord.id.desc())
        .limit(1)
    ).first()
    if record is None:
        raise ConflictError("未登记可用的签名密钥（请先运行 register_identity.py）")

    certificate = db.get(Certificate, record.cert_id)
    if certificate is None:
        raise ConflictError("签名密钥关联的证书不存在")
    if (record.algo or "").upper() != expected_algo:
        raise ConflictError(f"密钥算法与当前配置不一致（需要 {expected_algo}）")
    if (certificate.key_algo or "").upper() != expected_algo:
        raise ConflictError(f"证书算法与当前配置不一致（需要 {expected_algo}）")
    return SigningIdentity(key_record=record, certificate=certificate)


def load_signing_key(
    identity: SigningIdentity, passphrase: str, settings: Settings, *,
    now: datetime | None = None,
) -> KeyMaterial:
    """解锁私钥并校验证书（有效期/吊销/公钥一致）。

    口令错误 → `42200` 统一文案；证书不可用 → `40900`（带分类码，便于排障，但不含密钥信息）。
    """
    path = resolve_storage_path(identity.key_record.storage_path, settings)
    try:
        material = load_key_material(
            key_algo=identity.key_record.algo, storage_path=path, passphrase=passphrase
        )
    except KeyLoadError as exc:
        raise ValidationFailedError(_UNIFORM_KEY_ERROR) from exc

    certificate = identity.certificate
    problem = certificate_problem(
        key_algo=certificate.key_algo,
        cert_pem=certificate.cert_pem,
        public_material=material,
        revoked=certificate.revoked,
        now=now,
    )
    if problem is not None:
        raise ConflictError(f"签名证书不可用（{problem}）")
    return material


def get_finance_recipient(
    db: Session, provider: AlgoProvider, *, now: datetime | None = None
) -> tuple[Certificate, KeyMaterial]:
    """取当前有效的财务服务器证书（信封收方）；无可用证书 → 409。"""
    moment = now or datetime.now(timezone.utc)
    expected_algo = algo_names(provider).cert_algo
    certificate = db.scalars(
        select(Certificate)
        .where(
            Certificate.owner_type == CertOwnerType.SERVER.value,
            Certificate.revoked.is_(False),
            Certificate.not_before <= moment,
            Certificate.not_after >= moment,
        )
        .order_by(Certificate.id.desc())
        .limit(1)
    ).first()
    if certificate is None:
        raise ConflictError("没有可用的财务服务器证书（请先登记 server 证书）")
    if (certificate.key_algo or "").upper() != expected_algo:
        raise ConflictError(f"财务证书算法与当前配置不一致（需要 {expected_algo}）")

    try:
        material = recipient_key_material(
            key_algo=certificate.key_algo, cert_pem=certificate.cert_pem
        )
    except KeyLoadError as exc:
        raise ConflictError("财务证书公钥不可用") from exc
    return certificate, material