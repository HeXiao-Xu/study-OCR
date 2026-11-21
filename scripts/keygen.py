"""S4-5：生成 SM2 密钥对并做口令保护（OpenSSL 生成 → Python 加锁）。

用法（口令只从环境变量或交互输入，**不接受命令行参数**，避免进 shell 历史）：

    cd /e/BiSheXZY/study-OCR
    STUDYOCR_KEY_PASSPHRASE='demo-pass-1234' .venv/Scripts/python.exe \
        scripts/keygen.py --name alice --role submitter

产物（`secrets/` 已被 .gitignore 排除，绝不入库）：

    secrets/keys/alice/sign.p8.enc    口令加密的 PKCS#8（JSON：kdf/iterations/salt/nonce/ciphertext/key_fp）
    secrets/keys/alice/sign.pub.pem   公钥（PEM，可公开）

流程：`scripts/openssl.sh genpkey -algorithm SM2`（Docker 内 OpenSSL 3.5，D-078）
→ 取出 PKCS#8 DER → 与 `openssl pkey -pubout` 的公钥交叉核对
→ PBKDF2(200k) + AES-256-GCM 加锁 → 解锁 → 用解锁私钥签名并验签（自检）。
临时 PEM 用完即在 `finally` 删除；脚本输出**绝不含口令与私钥**。
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from Cryptodome.Util.asn1 import DerBitString, DerSequence  # noqa: E402

from app.crypto.keystore import (  # noqa: E402
    DEFAULT_ITERATIONS,
    MIN_ITERATIONS,
    load_keypair,
    private_key_hex_from_pkcs8,
    wrap_private_key,
)
from app.crypto.sm.sm2 import public_key_from_private, sign_bytes, verify_bytes  # noqa: E402

PASSPHRASE_ENV = "STUDYOCR_KEY_PASSPHRASE"
SELF_TEST_MESSAGE = b"study-ocr keystore self-test"
OPENSSL_IMAGE = os.environ.get("OPENSSL_IMAGE", "alpine/openssl:3.5.9")  # 与 scripts/openssl.sh 同镜像
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not _NAME_RE.match(args.name):
        _fail("--name 只能是小写字母/数字/下划线/短横线（1–32 位）")

    passphrase = _read_passphrase()
    out_dir = ROOT / args.out_dir / args.name
    key_file = out_dir / "sign.p8.enc"
    public_key_file = out_dir / "sign.pub.pem"
    if key_file.exists() and not args.force:
        _fail(f"{key_file.relative_to(ROOT)} 已存在；要覆盖请加 --force")

    if shutil.which("docker") is None:  # pragma: no cover - 环境缺陷
        _fail("找不到 docker：本机没有 openssl CLI，密钥由 Docker 里的 OpenSSL 生成（D-078）")
    _ensure_docker_running()
    out_dir.mkdir(parents=True, exist_ok=True)

    temp_pem = out_dir / f".tmp-{uuid.uuid4().hex}.pem"
    temp_pub_der = out_dir / f".tmp-{uuid.uuid4().hex}.pub.der"
    try:
        _run_openssl("genpkey", "-algorithm", "SM2", "-out", _rel(temp_pem))
        _run_openssl(
            "pkey", "-in", _rel(temp_pem), "-pubout", "-out", _rel(public_key_file)
        )
        _run_openssl(
            "pkey",
            "-in",
            _rel(temp_pem),
            "-pubout",
            "-outform",
            "DER",
            "-out",
            _rel(temp_pub_der),
        )

        pkcs8_der = _pem_to_der(temp_pem.read_text(encoding="utf-8"))
        private_key_hex = private_key_hex_from_pkcs8(pkcs8_der)
        derived_public = public_key_from_private(private_key_hex)
        openssl_public = _public_key_from_spki(temp_pub_der.read_bytes())
        if openssl_public != "04" + derived_public:
            _fail("公钥交叉核对失败：dG 推导结果与 OpenSSL 输出不一致")

        record = wrap_private_key(pkcs8_der, passphrase, iterations=args.iterations)
        key_file.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")

        keypair = load_keypair(record, passphrase)
        signature = sign_bytes(
            SELF_TEST_MESSAGE,
            private_key_hex=keypair.private_key_hex,
            public_key_hex=keypair.public_key_hex,
        )
        verified = verify_bytes(
            SELF_TEST_MESSAGE, signature, public_key_hex=keypair.public_key_hex
        )
        if not verified:
            _fail("解锁后签名自检失败（这不应该发生）")

        summary = {
            "name": args.name,
            "role": args.role,
            "algo": record.algo,
            "curve": record.curve,
            "kdf": record.kdf,
            "iterations": record.iterations,
            "key_fp": record.key_fp,
            "public_key_hex": keypair.public_key_hex,
            "private_key_file": key_file.relative_to(ROOT).as_posix(),
            "public_key_file": public_key_file.relative_to(ROOT).as_posix(),
            "self_test": "unlock+sign+verify ok",
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        # 明文私钥绝不留盘：无论成功失败都删临时 PEM / SPKI。
        for path in (temp_pem, temp_pub_der):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 SM2 密钥并对私钥做口令保护（S4-5）")
    parser.add_argument("--name", required=True, help="密钥名（小写字母/数字/_/-）")
    parser.add_argument("--role", default="submitter", help="角色备注（submitter/reviewer/admin/finance）")
    parser.add_argument("--out-dir", default="secrets/keys", help="输出目录（相对仓库根）")
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"PBKDF2 迭代次数（≥ {MIN_ITERATIONS}）",
    )
    parser.add_argument("--force", action="store_true", help="覆盖已存在的密钥文件")
    return parser.parse_args(argv)


def _read_passphrase() -> str:
    from_env = os.environ.get(PASSPHRASE_ENV)
    if from_env:
        return from_env
    first = getpass.getpass("请输入私钥口令（≥8 字符）：")
    second = getpass.getpass("再输入一次：")
    if first != second:
        _fail("两次输入的口令不一致")
    return first


def _run_openssl(*args: str) -> None:
    """直接调 docker，不经过 `scripts/openssl.sh`。

    为什么不复用脚本：Windows 上 Python 的子进程会优先找到 `C:\\Windows\\System32\\bash.exe`
    （WSL 的 bash），它转去找 `/bin/bash` 直接失败——本机踩过。这里按脚本里同样的参数
    调 docker，参数由 Python 直接传递，不经过任何 shell 的路径改写。
    """
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{ROOT}:/work",
            "-w",
            "/work",
            "--entrypoint",
            "openssl",
            OPENSSL_IMAGE,
            *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:400]
        _fail(f"openssl 执行失败（{args[0]}）：{detail}")


def _ensure_docker_running() -> None:
    completed = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        _fail("Docker 没在运行：密钥由容器里的 OpenSSL 生成，请先启动 Docker Desktop")


def _pem_to_der(pem_text: str) -> bytes:
    lines = [line.strip() for line in pem_text.strip().splitlines()]
    if not lines or not lines[0].startswith("-----BEGIN ") or not lines[-1].startswith("-----END "):
        _fail("OpenSSL 输出的不是 PEM")
    body = "".join(line for line in lines[1:-1] if line)
    try:
        return base64.b64decode(body, validate=True)
    except ValueError:
        _fail("PEM base64 解析失败")


def _public_key_from_spki(spki_der: bytes) -> str:
    """SubjectPublicKeyInfo → `04‖X‖Y` 的 hex（BIT STRING 首字节是 unused-bits=0）。"""
    sequence = DerSequence()
    sequence.decode(spki_der)
    payload = DerBitString().decode(sequence[1]).payload
    if len(payload) != 66 or payload[0] != 0 or payload[1] != 0x04:
        _fail("OpenSSL 公钥不是未压缩 SM2 点（04‖X‖Y）")
    return payload[1:].hex()


def _rel(path: Path) -> str:
    """容器内路径：`/work` = 仓库根，所以一律传仓库相对路径。"""
    return path.relative_to(ROOT).as_posix()


def _fail(message: str) -> None:
    print(f"错误：{message}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())