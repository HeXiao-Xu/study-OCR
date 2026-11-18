#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""study-OCR 环境冒烟检查（S0-7）。

一条命令回答"我这个环境到底行不行"：配置 → 三件套 → 后端 → （可选）前端。

设计原则：
1. **区分"没起"和"配错"**：连接被拒 = 服务没起（提示 `docker compose up -d`）；
   认证失败 / 库不存在 = 配置错（提示核对哪两个 .env）；这两种提示完全不一样。
2. **不吐 traceback**：任何检查失败都转成一行 `[FAIL] + 原因 + 下一步`，脚本自身出错也一样。
3. **参数全部来自 .env**：不硬编码端口与凭据；读 `backend/.env`（应用配置）与 `deploy/.env`（服务端口）。
4. **区分失败与待办**：S2-1 之前没有 `alembic_version` 表是正常的，记为 WARN；
   S2-1 之后跑同一脚本会自动升级为 FAIL（表存在即通过，缺失且已有迁移文件时才失败）。

用法：
    python scripts/smoke.py
    python scripts/smoke.py --strict     # WARN 也算失败（S2-1 之后建议用这个）
    python scripts/smoke.py --verbose    # 打印被检查的具体地址
退出码：0 全绿；1 有 FAIL（或 --strict 下有 WARN）。
"""
from __future__ import annotations

import argparse
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ENV = ROOT / "backend" / ".env"
DEPLOY_ENV = ROOT / "deploy" / ".env"

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"
_MARK = {OK: "[OK  ]", WARN: "[WARN]", FAIL: "[FAIL]", SKIP: "[SKIP]"}

results: list[tuple[str, str, str, str]] = []  # (等级, 项, 说明, 下一步)
VERBOSE = False


def record(level: str, name: str, detail: str, hint: str = "") -> None:
    results.append((level, name, detail, hint))
    print(f"{_MARK[level]} {name:<22} {detail}")
    if hint and level in (WARN, FAIL):
        print(f"       └─ {hint}")


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def load_env(path: Path) -> dict[str, str]:
    """最小 .env 解析：KEY=VALUE，# 注释，成对引号。"""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def mask_dsn(dsn: str) -> str:
    """把 postgresql://user:pass@host:port/db 里的口令打码，用于 verbose 输出。"""
    if "@" not in dsn:
        return dsn
    head, _, tail = dsn.partition("@")
    if "://" in head and ":" in head.split("://", 1)[1]:
        scheme_user, _, _password = head.partition(":")[0], None, None
        prefix, _, userinfo = head.partition("://")
        user = userinfo.split(":")[0]
        head = f"{prefix}://{user}:***"
        return f"{head}@{tail}"
    return dsn


# --------------------------------------------------------------------------
# 各项检查
# --------------------------------------------------------------------------
def check_config_files() -> dict[str, str]:
    missing = [p for p in (BACKEND_ENV, DEPLOY_ENV) if not p.exists()]
    if missing:
        names = "、".join(str(p.relative_to(ROOT)) for p in missing)
        record(
            FAIL,
            "配置文件",
            f"缺少 {names}",
            "首次使用请执行：cp backend/.env.example backend/.env && cp deploy/.env.example deploy/.env",
        )
        return {}
    record(OK, "配置文件", "backend/.env 与 deploy/.env 均存在")
    backend = load_env(BACKEND_ENV)
    deploy = load_env(DEPLOY_ENV)
    # 两个文件里的口令必须一致（常见故障：只改了一边）
    pairs = [
        ("MINIO_ACCESS_KEY", "MINIO_ROOT_USER"),
        ("MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"),
    ]
    for backend_key, deploy_key in pairs:
        a, b = backend.get(backend_key), deploy.get(deploy_key)
        if a and b and a != b:
            record(
                WARN,
                "配置一致性",
                f"{backend_key} 与 deploy 的 {deploy_key} 不一致",
                "两处必须相同，否则 MinIO 会返回 403 SignatureDoesNotMatch",
            )
            break
    else:
        record(OK, "配置一致性", "MinIO 凭据在 backend/.env 与 deploy/.env 中一致")
    return {**deploy, **backend}


def check_postgres(env: dict[str, str]) -> None:
    dsn = env.get("DATABASE_URL", "")
    if not dsn:
        record(FAIL, "PostgreSQL", "backend/.env 缺少 DATABASE_URL", "参考 backend/.env.example 补齐")
        return
    if VERBOSE:
        print(f"       · DSN: {mask_dsn(dsn)}")
    try:
        import psycopg
    except ImportError:
        record(FAIL, "PostgreSQL", "未安装 psycopg", "先建虚拟环境并安装依赖（见 README「快速开始」第 3 步）")
        return

    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("select version(), current_database()")
            version, database = cur.fetchone()
            cur.execute("select to_regclass('public.alembic_version')")
            alembic_table = cur.fetchone()[0]
    except psycopg.OperationalError as exc:
        message = str(exc).lower()
        if "does not exist" in message and "database" in message:
            record(FAIL, "PostgreSQL", f"库不存在：{message[:80]}", "检查 DATABASE_URL 里的库名，或重建数据卷")
        elif "password" in message or "authentication" in message:
            record(
                FAIL,
                "PostgreSQL",
                "认证失败",
                "backend/.env 的 DATABASE_URL 口令要与 deploy/.env 的 POSTGRES_PASSWORD 一致",
            )
        else:
            record(
                FAIL,
                "PostgreSQL",
                f"连不上：{message.splitlines()[0][:90]}",
                "服务可能没起：docker compose -f deploy/docker-compose.dev.yml up -d",
            )
        return
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本不许漏 traceback
        record(FAIL, "PostgreSQL", f"{type(exc).__name__}: {str(exc)[:90]}", "查看容器日志定位")
        return

    major = version.split(" ")[1].split(".")[0] if version.startswith("PostgreSQL") else "?"
    if major == "16":
        record(OK, "PostgreSQL", f"已连接，版本 16，库 {database}")
    else:
        record(WARN, "PostgreSQL", f"版本为 {major}，契约要求 16", "尽量对齐版本，避免 SQL 行为差异")

    if alembic_table:
        record(OK, "数据库迁移", "alembic_version 表存在")
        return
    has_migrations = any((ROOT / "backend" / "alembic" / "versions").glob("*.py"))
    if has_migrations:
        record(FAIL, "数据库迁移", "没有 alembic_version 表", "执行：cd backend && alembic upgrade head")
    else:
        record(
            WARN,
            "数据库迁移",
            "尚未建表（当前阶段正常）",
            "S2-1 起会生成首个迁移；届时这一项会变成必须通过",
        )


def check_redis(env: dict[str, str]) -> None:
    url = env.get("REDIS_URL", "")
    if not url:
        record(FAIL, "Redis", "backend/.env 缺少 REDIS_URL", "参考 backend/.env.example 补齐")
        return
    try:
        import redis
    except ImportError:
        record(FAIL, "Redis", "未安装 redis 客户端", "先安装后端依赖（见 README）")
        return
    if VERBOSE:
        print(f"       · URL: {mask_dsn(url)}")
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
        try:
            if not client.ping():
                record(FAIL, "Redis", "PING 未返回 PONG", "查看容器日志")
                return
            aof = client.config_get("appendonly").get("appendonly", "?")
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "authentication" in text or "noauth" in text or "wrongpass" in text:
            record(FAIL, "Redis", "认证失败", "REDIS_URL 里的口令要与 deploy/.env 的 REDIS_PASSWORD 一致")
        else:
            record(
                FAIL,
                "Redis",
                f"连不上：{str(exc).splitlines()[0][:90]}",
                "服务可能没起：docker compose -f deploy/docker-compose.dev.yml up -d",
            )
        return
    if aof == "yes":
        record(OK, "Redis", "PING 正常，appendonly=yes")
    else:
        record(WARN, "Redis", f"PING 正常，但 appendonly={aof}", "队列重启会丢任务；检查 compose 的 --appendonly yes")


def check_minio(env: dict[str, str]) -> None:
    endpoint = env.get("MINIO_ENDPOINT", "")
    access, secret = env.get("MINIO_ACCESS_KEY", ""), env.get("MINIO_SECRET_KEY", "")
    bucket = env.get("MINIO_BUCKET", "")
    if not all([endpoint, access, secret, bucket]):
        record(FAIL, "MinIO", "backend/.env 缺少 MINIO_* 配置", "参考 backend/.env.example 补齐")
        return
    try:
        from minio import Minio
    except ImportError:
        record(FAIL, "MinIO", "未安装 minio 客户端", "先安装后端依赖（见 README）")
        return
    if VERBOSE:
        print(f"       · endpoint={endpoint} bucket={bucket}")
    try:
        client = Minio(
            endpoint,
            access_key=access,
            secret_key=secret,
            secure=env.get("MINIO_SECURE", "false").lower() == "true",
        )
        exists = client.bucket_exists(bucket)
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "signaturedoesnotmatch" in text or "access denied" in text:
            record(FAIL, "MinIO", "认证失败", "MINIO_ACCESS_KEY/SECRET_KEY 要与 deploy/.env 的 ROOT 凭据一致")
        else:
            record(
                FAIL,
                "MinIO",
                f"连不上：{str(exc).splitlines()[0][:90]}",
                f"服务可能没起，或端口不是 {endpoint}：docker compose -f deploy/docker-compose.dev.yml up -d",
            )
        return
    if exists:
        record(OK, "MinIO", f"bucket {bucket} 存在")
    else:
        record(
            FAIL,
            "MinIO",
            f"bucket {bucket} 不存在",
            "跑一次初始化容器：docker compose -f deploy/docker-compose.dev.yml up -d minio-init",
        )


def _http_get(url: str, timeout: float) -> tuple[int, str]:
    with urlrequest.urlopen(url, timeout=timeout) as response:  # noqa: S310 - 本机固定地址
        return response.status, response.read(4096).decode("utf-8", errors="replace")


def check_backend(env: dict[str, str]) -> None:
    port = env.get("BACKEND_PORT", "8000")
    prefix = env.get("API_PREFIX", "/api/v1")
    url = f"http://127.0.0.1:{port}{prefix}/health"
    if VERBOSE:
        print(f"       · GET {url}")
    try:
        status, body = _http_get(url, timeout=3)
    except (urlerror.URLError, TimeoutError, OSError):
        record(
            FAIL,
            "后端 /health",
            f"{url} 不可达",
            "后端没起：cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000",
        )
        return
    if status == 200:
        record(OK, "后端 /health", "200，依赖全部正常")
    elif status == 503:
        record(
            FAIL,
            "后端 /health",
            "503 degraded（后端起着，但下游依赖有问题）",
            "看返回体里的 checks 字段，或先修上面的 PostgreSQL / Redis / MinIO 项",
        )
    else:
        record(WARN, "后端 /health", f"HTTP {status}：{body[:80]}", "检查后端日志")


def check_frontend(env: dict[str, str]) -> None:
    port = env.get("FRONTEND_PORT", "5173")
    url = f"http://127.0.0.1:{port}/"
    try:
        status, _ = _http_get(url, timeout=2)
    except (urlerror.URLError, TimeoutError, OSError):
        record(SKIP, "前端 dev server", f"{url} 未启动（前端不是环境硬依赖）", "需要时：cd web && npm run dev")
        return
    record(OK if status == 200 else WARN, "前端 dev server", f"{url} HTTP {status}")


def check_ports(env: dict[str, str]) -> None:
    """端口被别的进程占用是最常见的"服务没起却像是配错"的根因，单独报一次。"""
    candidates = [
        ("PostgreSQL", env.get("POSTGRES_PORT", "5433")),
        ("Redis", env.get("REDIS_PORT", "6380")),
        ("MinIO", env.get("MINIO_PORT", "9002")),
    ]
    busy: list[str] = []
    for name, port in candidates:
        if not port.isdigit():
            continue
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                busy.append(name)
        except OSError:
            pass
    if not busy:
        record(WARN, "端口监听", "三件套端口都没有进程在听", "先执行 docker compose up -d")
    else:
        record(OK, "端口监听", f"{'、'.join(busy)} 端口在监听")


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(description="study-OCR 环境冒烟检查（S0-7）")
    parser.add_argument("--strict", action="store_true", help="WARN 也算失败（S2-1 之后建议开启）")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印实际检查的地址/DSN（口令已打码）")
    args = parser.parse_args()
    VERBOSE = args.verbose

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 老终端不支持重配时忽略
        pass

    print("=" * 70)
    print(" study-OCR 环境冒烟检查")
    print(f" 仓库：{ROOT}")
    print("=" * 70)

    try:
        env = check_config_files()
        if env:
            check_ports(env)
            check_postgres(env)
            check_redis(env)
            check_minio(env)
            check_backend(env)
            check_frontend(env)
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本自身也不许吐 traceback
        record(FAIL, "冒烟脚本自身", f"{type(exc).__name__}: {str(exc)[:90]}", "这是脚本的 bug，请把这一行贴给开发者")

    ok = sum(1 for level, *_ in results if level == OK)
    warn = sum(1 for level, *_ in results if level == WARN)
    fail = sum(1 for level, *_ in results if level == FAIL)
    skip = sum(1 for level, *_ in results if level == SKIP)

    print("-" * 70)
    print(f"汇总：OK {ok} ｜ WARN {warn} ｜ FAIL {fail} ｜ SKIP {skip}")
    if fail:
        print("\n未通过项的处理顺序：配置 → 服务未起（docker compose up -d）→ 凭据不一致 → 后端/迁移")
        print("仍有问题请把上面的输出整段贴出来（已对凭据打码）。")
    elif warn:
        print("\n无致命错误。WARN 项在对应阶段生效为必须通过（例如 S2-1 之后的数据库迁移）。")
    else:
        print("\n全绿：环境可用。")

    failed = fail > 0 or (args.strict and warn > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())