"""把口令加密的 SM2 私钥（S4-5 的 `.p8.enc`）解锁成 PKCS#8 PEM，**只写到 stdout**。

存在理由：签发证书要让私钥对 CSR 签名，而 OpenSSL CLI 需要一个"能读到的密钥"。
本脚本把解锁结果直接送进管道（`ca_init.sh` 用 `openssl req -new -key /dev/stdin` 接收），
**私钥不会落盘**，也没有临时 PEM 文件。

用法（stdout 就是私钥 PEM，**绝不要重定向到文件**）：

    STUDYOCR_KEY_PASSPHRASE='...' .venv/Scripts/python.exe scripts/unlock_key.py --name alice \\
      | bash scripts/openssl.sh req -new -key /dev/stdin -sm3 -subj "/CN=alice" -out /tmp/alice.csr

口令来源：环境变量 `STUDYOCR_KEY_PASSPHRASE`，否则交互输入（`getpass`）。
失败时只往 stderr 打一行，**不输出任何密钥/口令**。
"""
from __future__ import annotations

import argparse
import base64
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.crypto.keystore import (  # noqa: E402
    EncryptedPrivateKey,
    KeystoreError,
    unwrap_private_key,
)

PASSPHRASE_ENV = "STUDYOCR_KEY_PASSPHRASE"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    key_file = ROOT / "secrets" / "keys" / args.name / "sign.p8.enc"
    if not key_file.is_file():
        _fail(f"找不到 {key_file.relative_to(ROOT)}：先跑 scripts/keygen.py --name {args.name}")

    try:
        record = EncryptedPrivateKey.model_validate_json(key_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        _fail(f"私钥文件格式不对：{exc}")

    try:
        der = unwrap_private_key(record, _read_passphrase())
    except KeystoreError as exc:
        _fail(str(exc))

    # stdout 是二进制 PEM，统一 LF，避免 Windows 文本模式加 CRLF 干扰管道。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")
    sys.stdout.write(_to_pkcs8_pem(der))
    sys.stdout.flush()
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="解锁 SM2 私钥并输出 PKCS#8 PEM 到 stdout（S4-6 签发用）")
    parser.add_argument("--name", required=True, help="密钥名（与 secrets/keys/<name>/ 对应）")
    return parser.parse_args(argv)


def _read_passphrase() -> str:
    from_env = os.environ.get(PASSPHRASE_ENV)
    if from_env:
        return from_env
    return getpass.getpass("请输入私钥口令：")


def _to_pkcs8_pem(der: bytes) -> str:
    body = base64.b64encode(der).decode("ascii")
    lines = [body[index : index + 64] for index in range(0, len(body), 64)]
    return "-----BEGIN PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END PRIVATE KEY-----\n"


def _fail(message: str) -> None:
    print(f"错误：{message}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())