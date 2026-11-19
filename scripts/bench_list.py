"""列表分页 5 万行基准（S2-4 验收）。

做的事情：
1. 在本地 PostgreSQL 建**一次性**库 `study_ocr_bench_<hex>`，跑迁移；
2. 用 `generate_series` 批量造 50,000 张票据 + 文件（不逐行 ORM）；
3. 用 `services/pagination.py` 跑四组场景，打印 p50 / p95；
4. `EXPLAIN ANALYZE` 主列表查询，检查是否走索引；
5. 删除临时库（`--keep` 保留）。

用法：
    cd /e/BiSheXZY/study-OCR
    .venv/Scripts/python.exe scripts/bench_list.py --rows 50000 --iterations 50
"""
from __future__ import annotations

import argparse
import math
import os
import secrets
import statistics
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.core.database import to_sqlalchemy_url  # noqa: E402
from app.models.invoices import Invoice  # noqa: E402
from app.models.users import User  # noqa: E402
from app.schemas.common import PageParams  # noqa: E402
from app.services.pagination import paginate  # noqa: E402
from app.services.repository import select_visible  # noqa: E402
from app.utils.text import escape_like  # noqa: E402

SORT_MAP = {
    "created_at": Invoice.created_at,
    "invoice_date": Invoice.invoice_date,
    "total_amount": Invoice.total_amount,
}


@dataclass(frozen=True)
class Scenario:
    name: str
    user_key: str
    status: str | None = None
    q: str | None = None
    page: int = 1
    page_size: int = 20
    sort_by: str = "created_at"
    order: str = "desc"


def _env_url() -> URL:
    env_file = BACKEND / ".env"
    values = dict(
        line.split("=", 1)
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    )
    return make_url(to_sqlalchemy_url(values["DATABASE_URL"]))


def _admin_engine(url: URL):
    return create_engine(
        url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )


def _psycopg_dsn(url: URL) -> str:
    """SQLAlchemy URL 带 `+psycopg` 方言，psycopg 直连要剥掉。"""
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@contextmanager
def _scratch_database(keep: bool) -> Iterator[tuple[str, URL]]:
    base = _env_url()
    name = f"study_ocr_bench_{secrets.token_hex(4)}"
    admin = _admin_engine(base)
    try:
        with admin.connect() as conn:
            conn.execute(sa_text(f'CREATE DATABASE "{name}"'))
        yield name, base.set(database=name)
    finally:
        admin.dispose()
        if keep:
            print(f"[keep] 临时库保留：{name}")
            return
        cleanup = _admin_engine(base)
        try:
            with cleanup.connect() as conn:
                conn.execute(sa_text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        except Exception as exc:  # noqa: BLE001 - 清理失败只告警，不影响测量结论
            print(f"[warn] 临时库 {name} 清理失败：{exc}")
        finally:
            cleanup.dispose()


def _migrate(url: URL) -> None:
    cfg = AlembicConfig(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    command.upgrade(cfg, "head")


def _seed(url: URL, rows: int) -> tuple[int, int]:
    """批量造数据，返回 (submitter_id, reviewer_id)。"""
    with psycopg.connect(_psycopg_dsn(url)) as conn:
        submitter = conn.execute(
            "insert into users (username, real_name, role, status)"
            " values ('bench_submitter', '基准用户', 'submitter', 'active') returning id"
        ).fetchone()[0]
        reviewer = conn.execute(
            "insert into users (username, real_name, role, status)"
            " values ('bench_reviewer', '基准审核', 'reviewer', 'active') returning id"
        ).fetchone()[0]

        # 文件与票据一一对应；sha256 / storage_key 用序号保证唯一
        conn.execute(
            """
            insert into invoice_files (uploader_id, filename, mime, size, sha256, storage_key)
            select %s, 'bench.jpg', 'image/jpeg', 1024,
                   lpad(to_hex(g), 64, '0'), 'bench/' || g
            from generate_series(1, %s) as g
            """,
            (submitter, rows),
        )
        conn.execute(
            """
            with f as (
                select id, row_number() over (order by id) as g
                from invoice_files where uploader_id = %s
            ), ins as (
                insert into invoices
                    (uploader_id, file_id, invoice_type, invoice_date, total_amount, status)
                select %s, f.id, 'vat_general',
                       current_date - ((f.g %% 365)::int),
                       ((f.g %% 50000) / 100.0)::numeric(12, 2),
                       'draft'
                from f
                returning id
            )
            select count(*) from ins
            """,
            (submitter, submitter),
        )
        # q 过滤需要发票号：按 id 补齐成 8 / 10 位，天然唯一
        conn.execute(
            "update invoices set"
            " invoice_no = lpad(id::text, 8, '0'),"
            " invoice_code = lpad(id::text, 10, '0')"
            " where uploader_id = %s",
            (submitter,),
        )
        # 造出不同状态，避免"全表同一值"让部分索引失真
        conn.execute(
            "update invoices set status = 'confirmed'"
            " where uploader_id = %s and (id %% 5) = 0",
            (submitter,),
        )
        conn.commit()
    return submitter, reviewer


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(ratio * len(ordered)) - 1))
    return ordered[index]


def _build_stmt(db: Session, user: User, scenario: Scenario):
    stmt = select_visible(Invoice, user)
    if scenario.status:
        stmt = stmt.where(Invoice.status == scenario.status)
    if scenario.q:
        stmt = stmt.where(Invoice.invoice_no.ilike(escape_like(scenario.q), escape="\\"))
    return stmt


def _run_scenario(
    session_factory,
    user_ids: dict[str, int],
    scenario: Scenario,
    iterations: int,
) -> tuple[list[float], int]:
    params = PageParams(
        page=scenario.page,
        page_size=scenario.page_size,
        sort_by=scenario.sort_by,
        order=scenario.order,  # type: ignore[arg-type]
    )
    timings: list[float] = []
    total = 0

    # 一次预热（排除首次连接与语句缓存的冷启动）
    with session_factory() as db:
        user = db.get(User, user_ids[scenario.user_key])
        paginate(db, _build_stmt(db, user, scenario), Invoice, SORT_MAP, params, default="created_at")

    for _ in range(iterations):
        with session_factory() as db:
            user = db.get(User, user_ids[scenario.user_key])
            started = time.perf_counter()
            result = paginate(
                db, _build_stmt(db, user, scenario), Invoice, SORT_MAP, params, default="created_at"
            )
            timings.append((time.perf_counter() - started) * 1000)
            total = result.total
    return timings, total


def _explain(url: URL, submitter_id: int) -> str:
    sql = (
        "explain (analyze, buffers, format text)"
        " select * from invoices where uploader_id = %s"
        " order by created_at desc, id asc limit 20 offset 0"
    )
    with psycopg.connect(_psycopg_dsn(url)) as conn:
        rows = conn.execute(sql, (submitter_id,)).fetchall()
    return "\n".join(row[0] for row in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="列表分页 5 万行基准")
    parser.add_argument("--rows", type=int, default=50000, help="票据行数（默认 50000）")
    parser.add_argument("--iterations", type=int, default=50, help="每组测量次数（默认 50）")
    parser.add_argument("--threshold-ms", type=float, default=300.0, help="p95 阈值（毫秒）")
    parser.add_argument("--keep", action="store_true", help="保留临时库用于排查")
    args = parser.parse_args(argv)

    with _scratch_database(keep=args.keep) as (name, url):
        print(f"temp db: {name}  rows: {args.rows}  iterations: {args.iterations}")
        _migrate(url)
        submitter_id, reviewer_id = _seed(url, args.rows)
        engine = create_engine(
            url.render_as_string(hide_password=False), poolclass=NullPool
        )
        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        user_ids = {"submitter": submitter_id, "reviewer": reviewer_id}

        scenarios = [
            Scenario("submitter + status=draft + created_at desc (page 1)", "submitter", status="draft"),
            Scenario("submitter + status=draft + deep page 1000", "submitter", status="draft", page=1000),
            Scenario("reviewer all rows + total_amount desc", "reviewer", sort_by="total_amount"),
            Scenario("submitter + q=invoice_no substring", "submitter", q="0001"),
        ]

        failures: list[str] = []
        print()
        print(f"{'scenario':<48}{'total':>8}{'p50(ms)':>10}{'p95(ms)':>10}")
        print("-" * 74)
        for scenario in scenarios:
            timings, total = _run_scenario(
                session_factory, user_ids, scenario, args.iterations
            )
            p50 = statistics.median(timings)
            p95 = _percentile(timings, 0.95)
            print(f"{scenario.name:<46}{total:>8}{p50:>10.1f}{p95:>10.1f}")
            if p95 > args.threshold_ms:
                failures.append(f"{scenario.name}: p95={p95:.1f}ms > {args.threshold_ms}ms")

        print()
        plan = _explain(url, submitter_id)
        print("EXPLAIN ANALYZE (submitter own rows, created_at desc, page 1):")
        for line in plan.splitlines():
            print("  " + line)
        used_index = "Index Scan" in plan or "Bitmap Index Scan" in plan
        if not used_index:
            failures.append("main list query did not use an index (no Index Scan in EXPLAIN)")
        elif not any(
            marker in plan
            for marker in ("ix_invoices_uploader_created", "ix_invoices_draft")
        ):
            print("[warn] an index was used, but not ix_invoices_uploader_created / ix_invoices_draft")

        engine.dispose()
        if failures:
            print("\nFAILED thresholds:")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print(f"\nPASS: all four p95 < {args.threshold_ms}ms and main list uses an index.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())