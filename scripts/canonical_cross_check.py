"""S4-1 跨语言验收：Python 侧规范化 + 摘要。

用法：
    cd /e/BiSheXZY/study-OCR
    .venv/Scripts/python.exe scripts/canonical_cross_check.py

与 `scripts/canonical_cross_check.mjs` 打印同样三行；两侧逐字相等才算跨语言成立。
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.crypto.canonical import canonical_json  # noqa: E402
from app.crypto.sm.sm3 import sm3_digest  # noqa: E402

FIXTURE = ROOT / "scripts" / "canonical_fixture.json"


def main() -> int:
    # 与 Node 侧逐字节可比：Windows 上默认会输出 \r\n，统一成 LF。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    canonical = canonical_json(payload)

    print(f"canonical_utf8_b64: {base64.b64encode(canonical).decode('ascii')}")
    print(f"sm3: {sm3_digest(canonical).hex()}")
    print(f"sha256: {hashlib.sha256(canonical).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())