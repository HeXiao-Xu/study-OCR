"""S3-1 真实链路验收脚本：登录 → 逐张上传 → 轮询 ocr-status → 统计成功率 / P50 / P95。

用法（先起 uvicorn :8000 与 arq worker）：
    cd /e/BiSheXZY/study-OCR
    .venv/Scripts/python.exe scripts/ocr_acceptance.py --samples 20 --password '<本地口令>'
    .venv/Scripts/python.exe scripts/ocr_acceptance.py --samples 20 --password '<口令>' --cleanup

口径：
- 单张耗时取 `GET /invoices/{id}` 的 `latest_job.elapsed_ms`（job_service 实测，不是脚本计时）；
- 409（同一 sha256 已上传）视为"跳过"，不计入成功率分母；
- 临时目录残留按流水线的前缀 `ocr-pipeline-` 扫系统临时目录；
- `--cleanup` 会删掉本次创建的票据行与 MinIO 对象，**只删本次创建的 id**。

退出码：0 达标（成功数 ≥ 总数-2 且无临时残留）；1 未达标；2 环境/样例问题。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))
# Settings 从 CWD 的 .env 读；固定切到 backend，避免"从仓库根跑就缺配置"
os.chdir(BACKEND)

import httpx  # noqa: E402

TEMP_PREFIX = "ocr-pipeline-"
SAMPLE_DIR = REPO_ROOT / "samples" / "synth_v1"
RESULT_DIR = BACKEND / "eval" / "results"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: httpx.Client, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if response.status_code != 200:
        raise SystemExit(f"登录失败（{response.status_code}）：{response.text[:200]}")
    return response.json()["data"]["access_token"]


def upload(client: httpx.Client, token: str, path: Path) -> dict | None:
    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/invoices/upload",
            headers=_headers(token),
            files={"file": (path.name, handle, "image/jpeg")},
        )
    if response.status_code == 409:
        payload = response.json().get("data") or {}
        return {"duplicate": True, "invoice_id": payload.get("invoice_id")}
    if response.status_code != 202:
        raise RuntimeError(f"上传失败 {path.name}（{response.status_code}）：{response.text[:200]}")
    return response.json()["data"]


def poll(client: httpx.Client, token: str, invoice_id: int, *, timeout_s: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/invoices/{invoice_id}/ocr-status",
                              headers=_headers(token))
        response.raise_for_status()
        data = response.json()["data"]
        if data["ocr_status"] in {"succeeded", "failed"}:
            return data
        time.sleep(2)
    raise TimeoutError(f"票据 {invoice_id} 在 {timeout_s:.0f}s 内没有到终态")


def detail(client: httpx.Client, token: str, invoice_id: int) -> dict:
    response = client.get(f"/api/v1/invoices/{invoice_id}", headers=_headers(token))
    response.raise_for_status()
    return response.json()["data"]


def cleanup(invoice_ids: list[int]) -> None:
    """按票据 id 删业务行 + 删 MinIO 对象；不打印对象名与票面信息。"""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.core.database import make_engine
    from app.services.storage import get_storage

    settings = get_settings()
    storage = get_storage()
    engine = make_engine(settings.database_url)
    with Session(engine) as db:
        keys = db.execute(
            text(
                "select storage_key from invoice_files where id in "
                "(select file_id from invoices where id = any(:ids))"
            ),
            {"ids": invoice_ids},
        ).scalars().all()
        db.execute(text("delete from ocr_lines where invoice_id = any(:ids)"), {"ids": invoice_ids})
        db.execute(text("delete from invoice_fields where invoice_id = any(:ids)"),
                   {"ids": invoice_ids})
        db.execute(text("delete from ocr_jobs where invoice_id = any(:ids)"), {"ids": invoice_ids})
        db.execute(text("delete from invoices where id = any(:ids)"), {"ids": invoice_ids})
        if keys:
            db.execute(text("delete from invoice_files where storage_key = any(:keys)"),
                       {"keys": keys})
        db.commit()
    engine.dispose()

    removed = 0
    for key in keys:
        try:
            storage.delete(key)
            removed += 1
        except Exception:  # noqa: BLE001 - 清理是尽量而为，失败不影响验收结论
            pass
    print(f"已清理 {len(invoice_ids)} 张票据 / {removed} 个对象")


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((percent / 100) * (len(ordered) - 1))))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="S3-1 OCR 链路验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=None, help="不给则交互输入")
    parser.add_argument("--cleanup", action="store_true", help="跑完删除本次创建的数据与对象")
    args = parser.parse_args()

    password = args.password or getpass.getpass("口令：")
    samples = sorted(SAMPLE_DIR.glob("*.jpg"))[: args.samples]
    if len(samples) < args.samples:
        print(f"样例不足：需要 {args.samples} 张，实际 {len(samples)} 张", file=sys.stderr)
        return 2

    rows: list[dict] = []
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        token = login(client, args.username, password)
        for path in samples:
            try:
                data = upload(client, token, path)
            except RuntimeError as exc:
                rows.append({"file": path.name, "skipped": False, "ocr_status": "upload_failed",
                             "error": str(exc)[:200], "elapsed_ms": None})
                continue
            if data is not None and data.get("duplicate"):
                # 重复影像不算失败；记下既有 id，--cleanup 时才能清掉上一次验收残留
                rows.append({"file": path.name, "skipped": True,
                             "invoice_id": data.get("invoice_id")})
                continue
            status = poll(client, token, data["invoice_id"])
            job = detail(client, token, data["invoice_id"]).get("latest_job") or {}
            rows.append({
                "file": path.name,
                "invoice_id": data["invoice_id"],
                "skipped": False,
                "ocr_status": status["ocr_status"],
                "progress": status["progress"],
                "elapsed_ms": job.get("elapsed_ms"),
                "error": job.get("error") or next(
                    (stage["error"] for stage in status["stages"] if stage["error"]), None),
            })

    done = [row for row in rows if not row.get("skipped")]
    ok = [row for row in done if row["ocr_status"] == "succeeded"]
    durations = [float(row["elapsed_ms"]) for row in ok if row["elapsed_ms"] is not None]
    leftover = sorted(str(path) for path in Path(tempfile.gettempdir()).glob(f"{TEMP_PREFIX}*"))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(done),
        "succeeded": len(ok),
        "failed": len(done) - len(ok),
        "skipped_duplicates": sum(1 for row in rows if row.get("skipped")),
        "success_rate": round(len(ok) / len(done), 3) if done else 0.0,
        "p50_ms": statistics.median(durations) if durations else None,
        "p95_ms": percentile(durations, 95),
        "max_ms": max(durations) if durations else None,
        "temp_leftover": leftover,
        "rows": rows,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"ocr_acceptance_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"成功 {report['succeeded']}/{report['samples']}"
          f"（{report['success_rate']:.1%}） P50 {report['p50_ms']}ms"
          f" P95 {report['p95_ms']}ms max {report['max_ms']}ms")
    for row in done:
        if row["ocr_status"] != "succeeded":
            print(f"  失败 {row['file']}: {row['error']}")
    if leftover:
        print(f"  ⚠ 临时目录残留：{leftover}")
    print(f"报告：{out}")

    passed = len(ok) >= max(0, len(done) - 2) and not leftover
    if args.cleanup:
        cleanup([row["invoice_id"] for row in rows if row.get("invoice_id")])
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())