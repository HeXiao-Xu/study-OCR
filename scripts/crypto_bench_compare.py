"""S4-9：国密 vs RSA/AES 的同接口对拍（给 S6 性能实验复用）。

用法（仓库根目录；Docker 需在跑——SM 密钥用容器内 OpenSSL 临时生成）：

    .venv/Scripts/python.exe scripts/crypto_bench_compare.py [--n 20] [--symmetric-kb 256]

测的操作（两套 provider 同名同参，才谈得上可比）：

| 操作 | 国密 | 对照 |
|---|---|---|
| sign / verify | SM2-SM3（DER） | RSA-PSS-SHA256 |
| encrypt_key / decrypt_key | SM2 包裹 16B CEK | RSA-OAEP-SHA256 |
| wrap_symmetric / unwrap_symmetric | SM4-CBC + HMAC-SM3（EtM） | AES-256-GCM |

输出 JSON（`results`）与人可读表格；只做相对比较，不承诺绝对性能。
SM 临时私钥目录在 `finally` 删除。
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import secrets
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.crypto.keystore import private_key_hex_from_pkcs8  # noqa: E402
from app.crypto.provider import RSAProvider, SMProvider  # noqa: E402
from app.crypto.sm.sm2 import public_key_from_private  # noqa: E402

IMAGE = os.environ.get("OPENSSL_IMAGE", "alpine/openssl:3.5.9")
SIGN_PAYLOAD = ('{"report_no":"RPT-202611-001","total_amount":"1068.00",'
                '"items":[{"amount":"100.00"},{"amount":"968.00"}]}').encode("utf-8")


def main() -> int:
    args = _parse_args()
    results: dict[str, dict[str, dict[str, float | int]]] = {}

    sm_key = _generate_sm_key()
    sm = SMProvider()
    rsa_providers = {f"rsa-{bits}": RSAProvider(bits) for bits in (2048, 3072)}
    rsa_keys = {name: provider.keygen() for name, provider in rsa_providers.items()}

    cek = secrets.token_bytes(16)
    plaintext = b"\x00" * (args.symmetric_kb * 1024)

    # ---- 非对称：签名 / 验签 / CEK 包裹 ----
    results["sm"] = _bench_provider(
        sm, sm_key, cek=cek, plaintext=plaintext, n=args.n, symmetric_n=max(3, args.n // 4)
    )
    for name, provider in rsa_providers.items():
        results[name] = _bench_provider(
            provider,
            rsa_keys[name],
            cek=cek,
            plaintext=plaintext,
            n=args.n,
            symmetric_n=max(3, args.n // 4),
        )

    # RSA keygen 也测一下（SM 密钥由 OpenSSL 生成，不在此口径内）
    for name, provider in rsa_providers.items():
        samples = [_time_ms(provider.keygen) for _ in range(max(3, args.n // 4))]
        results[name]["keygen"] = _summarize(samples)

    print(json.dumps({"config": {"n": args.n, "symmetric_kb": args.symmetric_kb}, "results": results}, indent=2))
    print()
    _print_table(results)
    return 0


def _bench_provider(provider, key, *, cek: bytes, plaintext: bytes, n: int, symmetric_n: int):
    signature = provider.sign(SIGN_PAYLOAD, key)
    wrapped = provider.encrypt_key(cek, key)
    symmetric_key = secrets.token_bytes(provider.symmetric_key_bytes)
    return {
        "sign": _summarize([_time_ms(provider.sign, SIGN_PAYLOAD, key) for _ in range(n)]),
        "verify": _summarize(
            [_time_ms(provider.verify, SIGN_PAYLOAD, signature, key) for _ in range(n)]
        ),
        "encrypt_key": _summarize([_time_ms(provider.encrypt_key, cek, key) for _ in range(n)]),
        "decrypt_key": _summarize(
            [_time_ms(provider.decrypt_key, wrapped, key) for _ in range(n)]
        ),
        "wrap_symmetric": _summarize(
            [_time_ms(provider.wrap_symmetric, symmetric_key, plaintext) for _ in range(symmetric_n)]
        ),
        "unwrap_symmetric": _summarize(
            [
                _time_ms(provider.unwrap_symmetric, symmetric_key, provider.wrap_symmetric(symmetric_key, plaintext))
                for _ in range(symmetric_n)
            ]
        ),
    }


def _summarize(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)], 3),
        "mean_ms": round(statistics.fmean(ordered), 3),
    }


def _time_ms(function, *args) -> float:
    started = time.perf_counter()
    function(*args)
    return (time.perf_counter() - started) * 1000.0


def _print_table(results: dict) -> None:
    print(f"{'provider':<10}{'operation':<18}{'n':>4}{'p50(ms)':>10}{'p95(ms)':>10}")
    for provider, operations in results.items():
        for operation, stats in operations.items():
            print(
                f"{provider:<10}{operation:<18}{stats['n']:>4}"
                f"{stats['p50_ms']:>10}{stats['p95_ms']:>10}"
            )


# ---------------------------------------------------------------- SM 密钥（OpenSSL 临时生成）


def _generate_sm_key():
    work = ROOT / f".tmp-bench-{uuid.uuid4().hex}"
    work.mkdir()
    try:
        _run_openssl(work, "genpkey", "-algorithm", "SM2", "-out", _rel(work / "key.pem"))
        der = _pem_to_der((work / "key.pem").read_text(encoding="utf-8"))
        private_hex = private_key_hex_from_pkcs8(der)
        public_hex = public_key_from_private(private_hex)
        return SMProvider.key_from_hex(private_hex, public_hex)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_openssl(work: Path, *args: str) -> None:
    result = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{ROOT}:/work", "-w", "/work",
            "--entrypoint", "openssl", IMAGE, *args,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"openssl 执行失败：{(result.stderr or result.stdout).strip()[:400]}")


def _pem_to_der(pem_text: str) -> bytes:
    lines = [line.strip() for line in pem_text.strip().splitlines()]
    return base64.b64decode("".join(line for line in lines[1:-1] if line), validate=True)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="国密 vs RSA/AES 同接口对拍（S4-9）")
    parser.add_argument("--n", type=int, default=20, help="非对称操作重复次数（默认 20）")
    parser.add_argument("--symmetric-kb", type=int, default=256, help="对称明文大小 KB（默认 256）")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())