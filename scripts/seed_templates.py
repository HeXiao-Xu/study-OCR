"""一次性 seed：把 S1 的 YAML 模板/规则搬进数据库（S2-8）。

幂等：`ticket_templates` 已有任何数据就跳过，绝不覆盖后台手工改过的模板。
用法：
    cd backend && ../.venv/Scripts/python.exe ../scripts/seed_templates.py --check
    cd backend && ../.venv/Scripts/python.exe ../scripts/seed_templates.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import ConfigError  # noqa: E402
from app.core.database import make_engine  # noqa: E402
from app.models.field_rules import FieldRule  # noqa: E402
from app.models.ticket_templates import TicketTemplate  # noqa: E402
from app.services.template_seed import build_seed_templates  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 S1 的 YAML 模板/规则 seed 进数据库（幂等）")
    parser.add_argument("--check", action="store_true", help="只打印将 seed 的内容，不写库")
    args = parser.parse_args(argv)

    try:
        seeds = build_seed_templates()
    except (FileNotFoundError, ValueError) as exc:
        print(f"seed 构建失败：{exc}", file=sys.stderr)
        return 2

    rules_count = sum(len(seed.rules) for seed in seeds)
    if args.check:
        print(f"seed 计划：{len(seeds)} 个模板 / {rules_count} 条规则")
        for seed in seeds:
            print(
                f"  - {seed.type_code}: {seed.type_name} | keywords={len(seed.match_keywords)}"
                f" | fields={len(seed.field_specs_json)} | rules={len(seed.rules)}"
            )
        return 0

    try:
        engine = make_engine()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        with Session(engine) as db:
            existing = db.scalar(select(func.count()).select_from(TicketTemplate)) or 0
            if existing:
                print(f"已有 {existing} 个模板，跳过 seed（不覆盖后台数据）")
                return 0

            for seed in seeds:
                template = TicketTemplate(
                    type_code=seed.type_code,
                    type_name=seed.type_name,
                    match_keywords=seed.match_keywords,
                    field_specs_json=seed.field_specs_json,
                    version=1,
                    active=True,
                )
                db.add(template)
                db.flush()
                for rule in seed.rules:
                    db.add(
                        FieldRule(
                            template_id=template.id,
                            field_name=rule.field_name,
                            pattern=rule.pattern,
                            validator=rule.validator,
                            error_msg=rule.error_msg,
                            priority=rule.priority,
                            active=True,
                        )
                    )
            db.commit()
            print(f"seed 完成：{len(seeds)} 个模板 / {rules_count} 条规则")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())