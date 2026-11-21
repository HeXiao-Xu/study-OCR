"""S4-8 跨实现验证：**我们的 SM2 签名 → OpenSSL 3.5 验签**。

为什么单独做成脚本：单元测试不该依赖 Docker（AGENTS §10.6），但"两边同时错也能通过"的循环自证
必须被打破一次——这里用任务卡要求的 OpenSSL 命令行独立验一次，结果记录到 PROGRESS。

用法（仓库根目录；Docker 需在跑）：

    .venv/Scripts/python.exe scripts/crypto_cross_check.py

流程：Docker 内 OpenSSL 生成 SM2 密钥 → 我们解出 d / 公钥 → `sign_bytes` 签名（DER）→
`openssl pkeyutl -verify -rawin -digest sm3 -pkeyopt distid:1234567812345678` 验证 → 清理临时目录。
**必须显式 distid**：OpenSSL 默认 SM2 ID 为空，不传会验不过（D-079/遗留 #88）。
临时目录在 `finally` 删除（里面是未加密私钥 PEM，绝不能留）。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.crypto.keystore import private_key_hex_from_pkcs8  # noqa: E402
from app.crypto.sm.sm2 import public_key_from_private, sign_bytes  # noqa: E402

IMAGE = os.environ.get("OPENSSL_IMAGE", "alpine/openssl:3.5.9")
MESSAGE = b"study-ocr S4-8 crypto cross-check"
# 国标默认用户标识（gmssl 与 OpenSSL 都要显式对齐，见 D-079）
DIST_ID = "1234567812345678"


def main() -> int:
    work = ROOT / f".tmp-cross-check-{uuid.uuid4().hex}"
    work.mkdir()
    try:
        _run_openssl(work, "genpkey", "-algorithm", "SM2", "-out", _rel(work / "key.pem"))
        _run_openssl(work, "pkey", "-in", _rel(work / "key.pem"), "-pubout", "-out", _rel(work / "pub.pem"))

        der = _pem_to_der((work / "key.pem").read_text(encoding="utf-8"))
        private_key_hex = private_key_hex_from_pkcs8(der)
        public_key_hex = public_key_from_private(private_key_hex)

        signature_hex = sign_bytes(
            MESSAGE, private_key_hex=private_key_hex, public_key_hex=public_key_hex
        )
        (work / "message.bin").write_bytes(MESSAGE)
        (work / "signature.der").write_bytes(bytes.fromhex(signature_hex))

        verify = _docker(
            "pkeyutl", "-verify",
            "-pubin", "-inkey", _rel(work / "pub.pem"),
            "-rawin", "-digest", "sm3",
            "-pkeyopt", f"distid:{DIST_ID}",
            "-in", _rel(work / "message.bin"),
            "-sigfile", _rel(work / "signature.der"),
            capture=True,
        )
        verified = verify.returncode == 0 and "Signature Verified Successfully" in (
            verify.stdout + verify.stderr
        )
        print(
            json.dumps(
                {
                    "ok": verified,
                    "openssl_verified": verified,
                    "openssl_output": (verify.stdout.strip() or verify.stderr.strip()),
                    "distid": DIST_ID,
                    "public_key_hex": public_key_hex,
                    "signature_der_hex_len": len(signature_hex),
                },
                ensure_ascii=False,
            )
        )
        return 0 if verified else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_openssl(work: Path, *args: str) -> None:
    result = _docker(*args, capture=True)
    if result.returncode != 0:
        raise SystemExit(f"openssl 执行失败（{args[0]}）：{(result.stderr or result.stdout).strip()[:400]}")


def _docker(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    if shutil.which("docker") is None:
        raise SystemExit("找不到 docker：交叉验证需要 Docker 里的 OpenSSL（D-078）")
    command = [
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{ROOT}:/work", "-w", "/work",
        "--entrypoint", "openssl", IMAGE, *args,
    ]
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=capture,
        text=True,
        check=False,
    )


def _pem_to_der(pem_text: str) -> bytes:
    lines = [line.strip() for line in pem_text.strip().splitlines()]
    if not lines or not lines[0].startswith("-----BEGIN ") or not lines[-1].startswith("-----END "):
        raise SystemExit("OpenSSL 输出的不是 PEM")
    return base64.b64decode("".join(line for line in lines[1:-1] if line), validate=True)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())