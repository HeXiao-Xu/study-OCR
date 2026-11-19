"""SQLAlchemy 声明式基类与建表约定。

为什么单独一层：
- **命名约定集中一处**：迁移里的约束名由 SQLAlchemy 生成，不手写，避免 `ck_`/`uq_` 飘移；
- **CHECK 文本助手**：十六进制摘要与 0–1 概率是全局约定（SCHEMA §0.3），
  各表复用同一段 SQL，避免 16 个文件里抄出 16 个版本；
- 不定义 relationship（spec §3.4）：关联查询由 service 显式写，避免懒加载语义与循环导入。
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, MetaData
from sqlalchemy.orm import DeclarativeBase

# 只在"没显式命名"时兜底；本项目的约束/索引全部显式命名，与 SCHEMA §0.1 对齐。
NAMING_CONVENTION: dict[str, str] = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def hex64_check(table: str, column: str) -> CheckConstraint:
    """64 位小写十六进制（SM3 / SHA-256 / 证书指纹），SCHEMA §0.3。

    可空列无需额外处理：SQL 的 CHECK 遇 NULL 结果为 UNKNOWN，按"通过"处理。
    """
    return CheckConstraint(
        f"{column} ~ '^[0-9a-f]{{64}}$'",
        name=f"ck_{table}_{column}",
    )


def probability_check(table: str, column: str) -> CheckConstraint:
    """置信度 / 分数必须落在 [0, 1]；NULL 放行（"未计算"与"算出来是 0"不同）。"""
    return CheckConstraint(
        f"{column} BETWEEN 0 AND 1",
        name=f"ck_{table}_{column}",
    )