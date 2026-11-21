"""S5-2：把证书与密钥**登记**进 `certificates` / `key_records`（幂等）。

为什么需要它：私钥绝不入库，库里只登记"谁、什么算法、证书在哪、密文私钥文件在哪"（SCHEMA §2.3）。
签名单据（`POST /reports/{id}/sign`）与封装信封（提交）都要先有这两条登记。

用法（在仓库根或 backend 都能跑；脚本自己切到 backend 读 `.env`）：

    # 用户签名身份（证书须由 scripts/ca_init.sh issue 签发）
    .venv/Scripts/python.exe scripts/register_identity.py --user alice
    # 财务服务器信封身份（owner_type=server，key_records.user_id 为空）
    .venv/Scripts/python.exe scripts/register_identity.py --server finance

产物：`certificates` 一行 + `key_records` 一行；重复执行只复用不重复插入。
**不打印私钥、不读私钥口令**（本脚本只登记路径）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # Settings 从 backend/.env 读，与其他脚本一致

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import session_scope  # noqa: E402
from app.crypto.certs import CertStatus, check_certificate, parse_certificate  # noqa: E402
from app.models.certificates import Certificate  # noqa: E402
from app.models.enums import CertOwnerType, KeyAlgo, KeyType  # noqa: E402
from app.models.key_records import KeyRecord  # noqa: E402
from app.models.users import User  # noqa: E402
from app.utils.text import normalize_username  # noqa: E402

_ALGO_CHOICES = (KeyAlgo.SM2.value, KeyAlgo.RSA.value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="登记证书与密钥（只写登记信息，私钥不入库）"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--user", help="用户名（登记为用户签名身份）")
    target.add_argument("--server", help="财务服务器标识（登记为 server 信封身份）")
    parser.add_argument("--algo", default=KeyAlgo.SM2.value, choices=_ALGO_CHOICES)
    parser.add_argument("--cert", help="证书路径（默认 secrets/certs/<name>.crt.pem）")
    parser.add_argument(
        "--key",
        help="口令加密私钥路径（默认 secrets/keys/<name>/sign.p8.enc）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    name = args.user or args.server
    owner_type = CertOwnerType.USER.value if args.user else CertOwnerType.SERVER.value

    cert_path = _resolve(args.cert, ROOT / "secrets/certs" / f"{name}.crt.pem")
    key_path = _resolve(args.key, ROOT / "secrets/keys" / name / "sign.p8.enc")
    if cert_path is None or not cert_path.is_file():
        return _fail(f"找不到证书：{cert_path}（先跑 bash scripts/ca_init.sh issue {name} server|submitter）")
    if key_path is None or not key_path.is_file():
        return _fail(f"找不到加密私钥：{key_path}（先跑 scripts/keygen.py --name {name}）")

    cert_pem = cert_path.read_bytes()
    try:
        info = parse_certificate(cert_pem)
    except ValueError as exc:
        return _fail(f"证书无法解析：{exc}")

    problem = _certificate_problem(cert_pem, info, args.algo)
    if problem is not None:
        return _fail(f"证书不可用：{problem}")

    public_key_pem = _public_key_pem(cert_path, key_path, args.algo)
    if public_key_pem is None:
        return _fail(
            "无法导出证书公钥 PEM：SM2 证书请确保 secrets/keys/<name>/sign.pub.pem 存在"
            "（cryptography 不认 SM2 公钥，这是 D-080 的已知限制）"
        )

    with session_scope() as db:
        user_id = None
        if owner_type == CertOwnerType.USER.value:
            user = db.scalars(
                select(User).where(User.username == normalize_username(name))
            ).first()
            if user is None:
                return _fail(f"用户 {name!r} 不存在（先跑 scripts/create_user.py）")
            user_id = user.id

        certificate = _upsert_certificate(
            db, info=info, owner_type=owner_type, owner_id=user_id,
            key_algo=args.algo, public_key_pem=public_key_pem, cert_pem=cert_pem,
        )
        key_record = _upsert_key_record(
            db, certificate_id=certificate.id, user_id=user_id,
            storage_path=_storage_path(key_path), key_type=(
                KeyType.SIGN.value if user_id is not None else KeyType.ENVELOPE.value
            ), algo=args.algo,
        )
        result = {
            "owner_type": owner_type,
            "owner": name,
            "certificate_id": certificate.id,
            "fingerprint_sha256": certificate.fingerprint_sha256,
            "subject_dn": certificate.subject_dn,
            "role_from_ou": info.role,
            "not_after": certificate.not_after.isoformat(),
            "key_record_id": key_record.id,
            "key_storage_path": key_record.storage_path,
            "algo": key_record.algo,
            "note": "私钥本体未入库；口令不在此脚本处理",
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _upsert_certificate(
    db: Session,
    *,
    info,
    owner_type: str,
    owner_id: int | None,
    key_algo: str,
    public_key_pem: str,
    cert_pem: bytes,
) -> Certificate:
    existing = db.scalars(
        select(Certificate).where(Certificate.fingerprint_sha256 == info.fingerprint_sha256)
    ).first()
    if existing is not None:
        return existing
    certificate = Certificate(
        owner_type=owner_type,
        owner_id=owner_id,
        subject_dn=info.subject_dn,
        serial_no=info.serial_no,
        issuer_dn=info.issuer_dn,
        key_algo=key_algo,
        public_key_pem=public_key_pem,
        cert_pem=cert_pem.decode("ascii"),
        not_before=info.not_before,
        not_after=info.not_after,
        fingerprint_sha256=info.fingerprint_sha256,
        revoked=False,
    )
    db.add(certificate)
    db.flush()
    return certificate


def _upsert_key_record(
    db: Session,
    *,
    certificate_id: int,
    user_id: int | None,
    storage_path: str,
    key_type: str,
    algo: str,
) -> KeyRecord:
    existing = db.scalars(
        select(KeyRecord).where(KeyRecord.storage_path == storage_path)
    ).first()
    if existing is not None:
        if existing.cert_id != certificate_id:
            raise SystemExit(
                f"错误：{storage_path} 已登记到证书 {existing.cert_id}，与本次证书 "
                f"{certificate_id} 不一致（先用 revoke 流程或人工处理）"
            )
        return existing
    record = KeyRecord(
        user_id=user_id,
        key_type=key_type,
        cert_id=certificate_id,
        storage_path=storage_path,
        algo=algo,
    )
    db.add(record)
    db.flush()
    return record


def _certificate_problem(cert_pem: bytes, info, algo: str) -> str | None:
    """登记前只做"能解析 + 未过期"；链校验本来就不在运行期做（D-080）。"""
    if algo == KeyAlgo.SM2.value:
        status = check_certificate(cert_pem, expected_public_key_hex=info.public_key_hex)
        return None if status is CertStatus.OK else status.value
    cert = x509.load_pem_x509_certificate(cert_pem)
    moment = datetime.now(timezone.utc)
    if moment < cert.not_valid_before_utc:
        return CertStatus.NOT_YET_VALID.value
    if moment > cert.not_valid_after_utc:
        return CertStatus.EXPIRED.value
    return None


def _public_key_pem(cert_path: Path, key_path: Path, algo: str) -> str | None:
    """取 `certificates.public_key_pem` 的值。

    SM2 不能走 `cert.public_key()`（cryptography 不认 sm2p256v1），直接用 keygen 产出的
    `sign.pub.pem`；RSA 才从证书里导出 SPKI PEM。
    """
    if algo == KeyAlgo.SM2.value:
        candidate = key_path.parent / "sign.pub.pem"
        if candidate.is_file():
            return candidate.read_text(encoding="ascii")
        return None
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except ValueError:
        return None
    return cert.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def _storage_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _resolve(raw: str | None, default: Path) -> Path | None:
    if raw is None:
        return default
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (ROOT / candidate)


def _fail(message: str) -> int:
    print(f"错误：{message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())