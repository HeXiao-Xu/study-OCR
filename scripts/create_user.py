"""初始化/运维脚本：创建账号。

为什么需要它：注册接口固定只产生 `submitter`；`reviewer` / `admin` 不允许自助注册
（API.md §1），只能由本脚本或管理动作创建。

用法（口令不给就交互输入，避免明文进 shell 历史）：
    cd backend
    ../.venv/Scripts/python.exe ../scripts/create_user.py \
        --username reviewer1 --role reviewer --real-name 审核员

退出码：0 成功；1 业务失败（如用户名已存在）；2 参数/配置错误。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))
# Settings 从 CWD 的 .env 读；固定切到 backend，避免"从仓库根跑就缺配置"
os.chdir(BACKEND)

from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import ConfigError  # noqa: E402
from app.core.database import make_engine  # noqa: E402
from app.core.exceptions import AppError  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.services.auth_service import create_user  # noqa: E402
from app.utils.text import normalize_username, username_error  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="创建系统账号（初始化/运维用，仅支持三角色）")
    parser.add_argument("--username", required=True, help="登录名（服务端 trim + 转小写）")
    parser.add_argument(
        "--role", required=True, choices=[member.value for member in UserRole]
    )
    parser.add_argument("--real-name", required=True, help="真实姓名")
    parser.add_argument("--dept", default=None, help="部门/学院（可选）")
    parser.add_argument(
        "--password", default=None, help="不给则交互输入；避免明文写进 shell 历史"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    problem = username_error(args.username)
    if problem:
        print(f"用户名不合法：{problem}", file=sys.stderr)
        return 2
    if not args.real_name.strip():
        print("真实姓名不能为空", file=sys.stderr)
        return 2

    password = args.password if args.password is not None else getpass.getpass("口令（≥8 位）：")
    if len(password) < 8:
        print("口令至少 8 位", file=sys.stderr)
        return 2

    try:
        engine = make_engine()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        with Session(engine) as db:
            try:
                user = create_user(
                    db,
                    username=normalize_username(args.username),
                    password=password,
                    real_name=args.real_name,
                    role=args.role,
                    dept=args.dept,
                )
            except AppError as exc:
                print(f"创建失败：{exc.message}", file=sys.stderr)
                return 1
            print(f"created id={user.id} username={user.username} role={user.role}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())