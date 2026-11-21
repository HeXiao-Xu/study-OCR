"""S5-7 端到端演示：上传 → OCR(确定性桩) → 确认 → 建单 → 签名/提交 → 验签/解密 → 审核 → 归档。

**说明**：OCR 用确定性桩引擎（固定票面），其余链路全真——HTTP 路由、OCR 作业执行路径、字段落库、
确认闸门、SM2/SM4 签名与信封、MinIO、Redis、PostgreSQL、审计哈希链。
真 Paddle 的端到端由 S3 测试与 `scripts/ocr_baseline.py` 覆盖；本脚本用于联调/答辩演示。

用法：
    python scripts/e2e_demo.py                 # 跑完清理业务行与对象，审计保留
    python scripts/e2e_demo.py --keep          # 保留业务行，便于打开 UI 查看
    python scripts/e2e_demo.py --passphrase demo-pass-1234

退出码：0 成功；2 环境不满足（缺用户/身份）；1 断言失败。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.api.deps import get_task_queue  # noqa: E402
from app.core.database import get_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.envelopes import Envelope  # noqa: E402
from app.models.invoice_files import InvoiceFile  # noqa: E402
from app.models.invoices import Invoice  # noqa: E402
from app.models.report_forms import ReportForm  # noqa: E402
from app.models.users import User  # noqa: E402
from app.ocr.types import OcrLine, OcrResult  # noqa: E402
from app.services import job_service  # noqa: E402
from app.services.audit import verify_chain  # noqa: E402
from app.services.ocr_pipeline import PipelineDeps, run_pipeline  # noqa: E402
from app.services.storage import get_storage  # noqa: E402

BASE = "/api/v1"


class _StubEngine:
    version = "stub"

    def recognize(self, _image) -> OcrResult:
        lines = [
            _line("电子发票（普通发票） 全电发票 数电票", 0, y=10),
            _line("发票号码：12345678901234567890", 1, y=120),
            _line("开票日期：2026-03-01", 2, y=160),
            _line("合计  ¥100.00  ¥13.00", 3, y=380),
            _line("价税合计（小写）￥113.00", 4, y=420),
        ]
        return OcrResult(lines=lines, image_size=(1000, 600), elapsed_ms=1.0)


def _line(text: str, line_no: int, *, y: float) -> OcrLine:
    return OcrLine(
        text=text,
        score=0.95,
        box=[[10.0, y], [600.0, y], [600.0, y + 30], [10.0, y + 30]],
        line_no=line_no,
        y_center=y + 15,
        x_left=10.0,
        height=30.0,
    )


class _Queue:
    def __init__(self) -> None:
        self.jobs: list[int] = []

    async def enqueue_ocr(self, job_id: int) -> None:
        self.jobs.append(job_id)


class _Lock:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    def acquire(self, key: str, _ttl: int) -> bool:
        if key in self.keys:
            return False
        self.keys.add(key)
        return True

    def release(self, key: str) -> None:
        self.keys.discard(key)


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def _token(user_id: int, role: str) -> str:
    from app.core.security import TokenType, encode_token

    token, _ = encode_token(
        user_id=user_id, role=role, token_type=TokenType.ACCESS, ttl_seconds=900
    )
    return token


class Demo:
    def __init__(self, client: TestClient, passphrase: str) -> None:
        self.client = client
        self.passphrase = passphrase
        self.rows: list[str] = []

    def step(self, label: str, response, *, expect: int) -> dict:
        assert response.status_code == expect, (
            f"{label} 期望 {expect}，实际 {response.status_code}：{response.text}"
        )
        trace = response.headers.get("x-trace-id", "-")
        print(f"[{label:<22}] HTTP {response.status_code}  trace_id={trace}")
        self.rows.append(f"{label}\t{response.status_code}\t{trace}")
        return response.json().get("data", {}) if response.content else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Study-OCR 端到端演示")
    parser.add_argument("--passphrase", default="demo-pass-1234", help="签名私钥口令")
    parser.add_argument("--keep", action="store_true", help="保留业务数据（不清理）")
    args = parser.parse_args()

    sf = get_session_factory()
    storage = get_storage()
    with sf() as db:
        users = {
            row.username: row
            for row in db.scalars(
                select(User).where(User.username.in_(["alice", "bob", "root"]))
            ).all()
        }
        cert_count = db.scalar(text("select count(*) from certificates"))
        key_count = db.scalar(text("select count(*) from key_records"))
    if set(users) != {"alice", "bob", "root"} or cert_count < 2 or key_count < 2:
        print(
            "环境不满足：需要 alice/bob/root 三个用户 + 已登记证书/密钥。\n"
            "  python scripts/create_user.py ...\n"
            "  python scripts/register_identity.py ...",
            file=sys.stderr,
        )
        return 2

    app = create_app()
    app.dependency_overrides[get_task_queue] = lambda: _Queue()
    report_id = invoice_id = file_id = preview_token = None
    uploaded_key = None
    cipher_key = None
    status = 0

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            demo = Demo(client, args.passphrase)
            alice, bob, root = users["alice"], users["bob"], users["root"]
            sh = {"Authorization": f"Bearer {_token(alice.id, 'submitter')}"}
            rh = {"Authorization": f"Bearer {_token(bob.id, 'reviewer')}"}
            ah = {"Authorization": f"Bearer {_token(root.id, 'admin')}"}

            jpeg = _jpeg()
            upload = demo.step(
                "upload",
                client.post(
                    f"{BASE}/invoices/upload",
                    files={"file": ("demo.jpg", jpeg, "image/jpeg")},
                    headers=sh,
                ),
                expect=202,
            )
            invoice_id = upload["invoice_id"]

            outcome = job_service.execute_job_attempt(
                upload["job_id"],
                attempt=1,
                handler=lambda db, job: run_pipeline(
                    db,
                    job,
                    deps=PipelineDeps(
                        engine=_StubEngine(),
                        storage=storage,
                        prepare_pages=None,
                        templates=None,
                        registry=None,
                        clock=lambda: 1.0,
                    ),
                ),
                session_factory=sf,
                lock_store=_Lock(),
            )
            assert outcome == "succeeded", f"OCR 作业失败：{outcome}"
            print(f"[{'ocr(stub)':<22}] outcome=succeeded")
            demo.rows.append(f"ocr(stub)\tsucceeded\t-")

            with sf() as db:
                db.execute(
                    text(
                        "update invoice_fields set confirmed = true, need_review = false"
                        " where invoice_id = :id"
                    ),
                    {"id": invoice_id},
                )
                db.commit()
                version = db.get(Invoice, invoice_id).version
                invoice = db.get(Invoice, invoice_id)
                total = f"{invoice.total_amount:.2f}"

            demo.step(
                "confirm",
                client.post(
                    f"{BASE}/invoices/{invoice_id}/confirm",
                    json={"version": version},
                    headers=sh,
                ),
                expect=200,
            )

            created = demo.step(
                "create_report",
                client.post(
                    f"{BASE}/reports",
                    json={"title": "E2E 演示报销单", "reason": "端到端联调"},
                    headers=sh,
                ),
                expect=201,
            )
            report_id = created["id"]
            demo.step(
                "add_item",
                client.post(
                    f"{BASE}/reports/{report_id}/items",
                    json={"invoice_id": invoice_id, "amount": total, "remark": "交通"},
                    headers=sh,
                ),
                expect=201,
            )
            demo.step(
                "sign",
                client.post(
                    f"{BASE}/reports/{report_id}/sign",
                    json={"private_key_passphrase": args.passphrase},
                    headers=sh,
                ),
                expect=200,
            )
            demo.step(
                "submit",
                client.post(
                    f"{BASE}/reports/{report_id}/submit",
                    json={"idempotency_key": f"e2e-demo-{secrets.token_hex(6)}"},
                    headers=sh,
                ),
                expect=200,
            )

            verified = demo.step(
                "verify",
                client.post(f"{BASE}/finance/reports/{report_id}/verify", headers=rh),
                expect=200,
            )
            decrypted = demo.step(
                "decrypt",
                client.post(
                    f"{BASE}/finance/reports/{report_id}/decrypt",
                    json={"verify_token": verified["verify_token"]},
                    headers=rh,
                ),
                expect=200,
            )
            preview_token = decrypted["preview_token"]
            preview = client.get(f"{BASE}/finance/preview/{preview_token}", headers=rh)
            assert preview.status_code == 200
            print(f"[{'preview':<22}] HTTP 200  report_no={preview.json()['report_no']}")

            with sf() as db:
                form = db.get(ReportForm, report_id)
                version = form.version
                invoice_file_id = db.scalar(
                    text("select file_id from invoices where id = :id"), {"id": invoice_id}
                )
            file_id = invoice_file_id
            with sf() as db:
                form_env = db.scalars(
                    select(Envelope).where(Envelope.form_id == report_id)
                ).first()
            cipher_key = form_env.ciphertext_storage_key if form_env else None

            demo.step(
                "approve",
                client.post(
                    f"{BASE}/finance/reports/{report_id}/review",
                    json={
                        "decision": "approve",
                        "version": version,
                        "verify_token": verified["verify_token"],
                    },
                    headers=rh,
                ),
                expect=200,
            )
            archived = demo.step(
                "archive",
                client.post(
                    f"{BASE}/admin/reports/{report_id}/archive",
                    json={"confirm": True},
                    headers=ah,
                ),
                expect=200,
            )

        with sf() as db:
            chain = verify_chain(db)
            form = db.get(ReportForm, report_id)
            print(
                json.dumps(
                    {
                        "report_no": form.report_no,
                        "archive_no": archived["archive_no"],
                        "status": form.status,
                        "audit_ok": chain.ok,
                        "audit_count": chain.checked_count,
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    except AssertionError as exc:
        status = 1
        print(f"FAILED: {exc}", file=sys.stderr)
        return status
    finally:
        if args.keep:
            print("保留业务数据（--keep）")
            return status
        try:
            with sf() as db:
                file_row = db.get(InvoiceFile, file_id) if file_id else None
                uploaded_key = file_row.storage_key if file_row else uploaded_key
                envelopes = db.scalars(
                    select(Envelope).where(
                        (Envelope.form_id == report_id) | (Envelope.file_id == file_id)
                    )
                ).all()
                keys = [row.ciphertext_storage_key for row in envelopes if row.ciphertext_storage_key]
                if report_id:
                    db.execute(text("delete from review_logs where form_id = :r"), {"r": report_id})
                    db.execute(
                        text("delete from envelopes where form_id = :r or file_id = :f"),
                        {"r": report_id, "f": file_id},
                    )
                    db.execute(text("delete from report_items where form_id = :r"), {"r": report_id})
                    db.execute(text("delete from signatures where form_id = :r"), {"r": report_id})
                    db.execute(text("delete from report_forms where id = :r"), {"r": report_id})
                if invoice_id:
                    db.execute(text("delete from ocr_lines where invoice_id = :i"), {"i": invoice_id})
                    db.execute(text("delete from ocr_jobs where invoice_id = :i"), {"i": invoice_id})
                    db.execute(
                        text("delete from invoice_fields where invoice_id = :i"), {"i": invoice_id}
                    )
                    db.execute(text("delete from invoices where id = :i"), {"i": invoice_id})
                if file_id:
                    db.execute(text("delete from invoice_files where id = :f"), {"f": file_id})
                db.commit()
            for key in {*keys, uploaded_key}:
                if key:
                    storage.delete(key)
            if preview_token:
                prefix = f"previews/{preview_token}"
                for key, _modified in storage.list_objects(prefix):
                    storage.delete(key)
            print("清理：业务行/对象已删除，审计保留")
        except Exception as exc:  # noqa: BLE001
            print(f"清理失败：{exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())