"""轻量文本规范化助手。

用户名规则（API.md §1）：trim + 转小写后 3–64 字符、不含空白。
注册接口（Pydantic 校验）与初始化脚本共用同一份实现，避免"接口能注册、脚本建不出来"。
"""
from __future__ import annotations

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 64


def normalize_username(raw: str) -> str:
    return raw.strip().lower()


def username_error(raw: str) -> str | None:
    """返回人类可读的错误原因；合法返回 None。"""
    value = normalize_username(raw)
    if not value:
        return "用户名不能为空"
    if not USERNAME_MIN_LENGTH <= len(value) <= USERNAME_MAX_LENGTH:
        return f"用户名长度必须为 {USERNAME_MIN_LENGTH}–{USERNAME_MAX_LENGTH} 个字符"
    if any(char.isspace() for char in value):
        return "用户名不能包含空白字符"
    return None