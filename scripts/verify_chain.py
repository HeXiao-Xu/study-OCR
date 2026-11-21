"""S4-7：审计日志哈希链全量校验（命令行）。

用法（仓库根目录）：

    .venv/Scripts/python.exe scripts/verify_chain.py

读 `backend/.env` 的配置（含 `AUDIT_HMAC_KEY`），连库后按 `id` 升序复算整条链：

- 链完整：打印 `{"ok": true, "checked_count": N, ...}`，退出码 0；
- 断链：打印首个断点的 `broken_at / broken_id / reason`，退出码 1（便于 CI/运维告警）。

只读：不写审计行、不改链、不打印审计 key。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # Settings 从 backend/.env 读，与其他脚本一致

from app.core.database import session_scope  # noqa: E402
from app.services.audit import verify_chain  # noqa: E402


def main() -> int:
    with session_scope() as db:
        result = verify_chain(db)

    print(
        json.dumps(
            {
                "ok": result.ok,
                "checked_count": result.checked_count,
                "broken_at": result.broken_at,
                "broken_id": result.broken_id,
                "reason": result.reason,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())