#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S0-4 基础服务连通性自检。

检查 deploy/docker-compose.dev.yml 起的三件套是否真的可用（不只是端口通）：
  1. docker compose 可用、compose 文件能解析
  2. PostgreSQL：执行 `select 1`，并确认 init SQL 里的 pgcrypto 已装上
  3. Redis：AUTH + PING，并确认 appendonly=yes（队列重启不丢）
  4. MinIO：健康探针 + 真实对象写入/读取/删除（bucket 由 minio-init 幂等创建）

只依赖 Python 标准库（S0-4 时后端虚拟环境还没建）；对 PG / MinIO 的操作通过
`docker compose exec` / `docker compose run` 在容器内执行 —— 这样验证的是**容器本身**，
而不是宿主机上某个恰好占了同端口的进程。

用法：
    python scripts/check_services.py
    python scripts/check_services.py --verbose     # 打印每条实际执行的命令
退出码：0 全部通过；1 有失败项。
"""
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "docker-compose.dev.yml"
ENV_FILE = ROOT / "deploy" / ".env"

VERBOSE = False
results: list[tuple[str, bool, str]] = []


def out(msg: str = "") -> None:
    print(msg)


def load_env(path: Path) -> dict[str, str]:
    """最小 .env 解析：KEY=VALUE，忽略注释与空行，去掉成对引号。"""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    """执行命令，返回 (退出码, 合并输出)。不抛异常，交由调用方判定。"""
    if VERBOSE:
        out("      $ " + " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except FileNotFoundError:
        return 127, "命令不存在：" + cmd[0]
    except subprocess.TimeoutExpired:
        return 124, f"超时（{timeout}s）"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def compose(*args: str, timeout: int = 60) -> tuple[int, str]:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", str(ENV_FILE), *args]
    return run(cmd, timeout=timeout)


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    out(f"[{'OK  ' if ok else 'FAIL'}] {name:<22} {detail}")


# --------------------------------------------------------------------------
# 1. Docker / compose
# --------------------------------------------------------------------------
def check_docker() -> bool:
    if shutil.which("docker") is None:
        record("docker compose", False, "找不到 docker 命令（Docker Desktop 未装或未加入 PATH）")
        return False
    rc, txt = run(["docker", "compose", "version", "--short"])
    if rc != 0:
        record("docker compose", False, txt.strip()[:120] or "docker compose 不可用")
        return False
    rc2, txt2 = compose("config", "--quiet")
    if rc2 != 0:
        record("docker compose", False, "compose 文件解析失败：" + txt2.strip()[:160])
        return False
    record("docker compose", True, txt.strip().splitlines()[0])
    return True


# --------------------------------------------------------------------------
# 2. PostgreSQL
# --------------------------------------------------------------------------
def check_postgres(env: dict[str, str]) -> None:
    user = env.get("POSTGRES_USER", "studyocr")
    db = env.get("POSTGRES_DB", "studyocr")
    port = env.get("POSTGRES_PORT", "5433")
    rc, txt = compose(
        "exec", "-T", "postgres", "psql", "-U", user, "-d", db, "-tAc", "select 1", timeout=30
    )
    if rc != 0 or txt.strip() != "1":
        record("PostgreSQL 16", False, f"{port} -> select 1 失败：{txt.strip()[:120]}")
        return
    rc2, txt2 = compose(
        "exec", "-T", "postgres", "psql", "-U", user, "-d", db, "-tAc",
        "select count(*) from pg_extension where extname = 'pgcrypto'", timeout=30,
    )
    ext_ok = rc2 == 0 and txt2.strip() == "1"
    detail = f"{port} -> select 1 = 1；pgcrypto {'已安装' if ext_ok else '未安装'}"
    if not ext_ok:
        detail += "（init SQL 可能没执行：只有数据卷首次创建时才跑）"
    record("PostgreSQL 16", ext_ok, detail)


# --------------------------------------------------------------------------
# 3. Redis（RESP 协议手搓，避免引入 redis 客户端依赖）
# --------------------------------------------------------------------------
def _resp_send(sock: socket.socket, *parts: str) -> str:
    payload = b"*" + str(len(parts)).encode() + b"\r\n"
    for part in parts:
        raw = part.encode("utf-8")
        payload += b"$" + str(len(raw)).encode() + b"\r\n" + raw + b"\r\n"
    sock.sendall(payload)
    return sock.recv(4096).decode("utf-8", errors="replace").strip()


def check_redis(env: dict[str, str]) -> None:
    port = int(env.get("REDIS_PORT", "6380"))
    password = env.get("REDIS_PASSWORD", "")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            if password:
                auth = _resp_send(sock, "AUTH", password)
                if not auth.startswith("+OK"):
                    record("Redis 7", False, f"{port} -> AUTH 失败：{auth[:80]}")
                    return
            pong = _resp_send(sock, "PING")
            if pong != "+PONG":
                record("Redis 7", False, f"{port} -> PING 返回 {pong[:80]}")
                return
            aof = _resp_send(sock, "CONFIG", "GET", "appendonly")
    except OSError as exc:
        record("Redis 7", False, f"{port} -> 连不上：{exc}")
        return
    aof_on = "yes" in aof.lower()
    record(
        "Redis 7",
        aof_on,
        f"{port} -> PONG；appendonly={'yes' if aof_on else 'no（队列重启会丢，检查 compose 的 --appendonly）'}",
    )


# --------------------------------------------------------------------------
# 4. MinIO：健康探针 + 真实对象读写
# --------------------------------------------------------------------------
def check_minio_health(env: dict[str, str]) -> None:
    from urllib import request as urlrequest

    port = env.get("MINIO_PORT", "9002")
    url = f"http://127.0.0.1:{port}/minio/health/live"
    try:
        with urlrequest.urlopen(url, timeout=5) as resp:  # noqa: S310 - 本机固定地址
            code = resp.status
    except Exception as exc:  # noqa: BLE001 - 探针失败任何异常都算失败
        record("MinIO 健康", False, f"{port} -> 探针失败：{exc}")
        return
    record("MinIO 健康", code == 200, f"{port} -> /minio/health/live {code}")


MINIO_OBJECT_SCRIPT = r"""
set -eu
mc alias set local "$MINIO_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
printf 'study-ocr healthcheck\n' > /tmp/hc.txt
mc cp /tmp/hc.txt "local/$MINIO_BUCKET/_healthcheck/hc.txt" >/dev/null
mc stat "local/$MINIO_BUCKET/_healthcheck/hc.txt" >/dev/null
mc rm "local/$MINIO_BUCKET/_healthcheck/hc.txt" >/dev/null
echo OBJECT_CRUD_OK
"""


def check_minio_object(env: dict[str, str]) -> None:
    bucket = env.get("MINIO_BUCKET", "study-ocr-invoices")
    rc, txt = compose(
        "run", "--rm", "-T", "--no-deps", "--entrypoint", "/bin/sh", "minio-init",
        "-c", MINIO_OBJECT_SCRIPT, timeout=90,
    )
    if rc != 0 or "OBJECT_CRUD_OK" not in txt:
        record("MinIO 对象读写", False, f"bucket={bucket} -> 失败：{txt.strip()[:160]}")
        return
    record("MinIO 对象读写", True, f"bucket={bucket} -> 写入/读取/删除 通过")


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(description="study-OCR 基础服务连通性自检（S0-4）")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印实际执行的命令")
    args = parser.parse_args()
    VERBOSE = args.verbose

    try:  # Windows 控制台默认 GBK，中文/符号可能编码失败，这里兜底
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    out("=" * 66)
    out(" study-OCR 基础服务自检")
    out(f" compose: {COMPOSE_FILE.relative_to(ROOT)}")
    out(f" env    : {ENV_FILE.relative_to(ROOT)}")
    out("=" * 66)

    if not ENV_FILE.exists():
        out("")
        out(f"[FAIL] 缺少 {ENV_FILE.relative_to(ROOT)}：先执行 `cp deploy/.env.example deploy/.env`")
        return 1

    env = load_env(ENV_FILE)

    if not check_docker():
        out("")
        out("结论：docker compose 不可用，后续检查跳过。请先启动 Docker Desktop。")
        return 1

    check_postgres(env)
    check_redis(env)
    check_minio_health(env)
    check_minio_object(env)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    out("")
    out(f"结论：{passed}/{total} 通过")
    if passed != total:
        out("")
        out("排查建议：")
        out("  1) `docker compose -f deploy/docker-compose.dev.yml ps` 看容器是否 healthy")
        out("  2) `docker compose -f deploy/docker-compose.dev.yml logs --tail=50 postgres`")
        out("  3) 端口被占用？改 deploy/.env 里的 *_PORT（本机 study-agent 占着 5432/6379/9000/9001）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())