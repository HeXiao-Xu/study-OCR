"""Canonical JSON：签名域的**唯一**字节串表示。

为什么需要它：签名/摘要的对象必须逐字节可复现。Python 的 `json.dumps` 默认
`", "`/`": "` 分隔、按插入序输出、`ensure_ascii=True`、且会把 `100.0` 写成 `100.0`；
同一条业务数据换个进程、换个语言就会算出不同摘要——**签名前不做规范化，验签必然失败**。

本模块把口径定死（`docs/spec/s4-1-canonical-sm3.md` §3，S5 不再修改）：

- 键按 **Unicode 码点**升序；分隔符 `,` `:` 无空格；`ensure_ascii=False` 后 UTF-8；
- 只接受 JSON 数据模型内的类型：`None` / `bool` / `int`（安全整数）/ `str` / `list` / `dict[str, ...]`；
- **拒绝 float 与 Decimal**：`100`、`100.0`、`100.00` 会产出三种字节，金额一律由调用方先格式化成字符串；
- 拒绝非 `str` 键（不做隐式 `str()`，否则 `1` 与 `"1"` 撞车）；
- 拒绝 `|n| > 2^53-1`：JS 的 `Number` 只能精确到 2^53-1，放行会让跨语言摘要静默分叉。

实现上先做一遍校验遍历，再交给 `json.dumps` 负责转义细节——转义规则（短转义、
`\\u00XX`、循环检测）由标准库保证，比手写更不容易与 JSON 标准分叉；标准库**不管**的
类型边界由校验遍历补齐。
"""
from __future__ import annotations

import hashlib
import json
from typing import Final

__all__ = ["CanonicalJSONError", "canonical_json", "canonical_sha256"]

# JS 的 Number.MAX_SAFE_INTEGER；超出这个范围，Node 端无法精确表示同一个整数。
MAX_SAFE_INT: Final = 2**53 - 1


class CanonicalJSONError(ValueError):
    """输入不在 canonical JSON 允许的数据模型内（或无法编码成 UTF-8）。"""


def canonical_json(obj: object) -> bytes:
    """把业务对象序列化成唯一的 UTF-8 字节串（无 BOM、无尾随换行）。"""
    _validate(obj, path="$", stack=set())
    try:
        text = json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,  # 校验已挡掉 float；这里是第二道闸（防未来改动放行）
        )
        return text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:  # 循环引用 / 代理项 / 编码失败
        raise CanonicalJSONError(f"canonical_json 序列化失败：{exc}") from exc


def canonical_sha256(obj: object) -> str:
    """同口径 SHA-256（64 位小写 hex），仅作跨实现对照，不进国密签名链路。"""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def _validate(obj: object, *, path: str, stack: set[int]) -> None:
    """递归校验；只报类型与路径，**不回显字段值**（金额/税号不进异常信息）。"""
    if obj is None:
        return
    # bool 必须先于 int 判断：Python 里 True 是 int 的子类，否则会被写成 1。
    if isinstance(obj, bool):
        return
    if isinstance(obj, int):
        if obj > MAX_SAFE_INT or obj < -MAX_SAFE_INT:
            raise CanonicalJSONError(
                f"{path}: 整数超出跨语言安全范围 ±(2^53-1)，请先转成字符串"
            )
        return
    if isinstance(obj, str):
        _ensure_encodable(obj, path)
        return
    if isinstance(obj, float):
        raise CanonicalJSONError(
            f"{path}: 禁止 float（会与 100/100.00 产生不同字节），金额请先格式化成字符串"
        )
    if isinstance(obj, tuple):
        raise CanonicalJSONError(f"{path}: 只支持 list，不支持 tuple")
    if isinstance(obj, list):
        _enter(obj, path, stack)
        for index, item in enumerate(obj):
            _validate(item, path=f"{path}[{index}]", stack=stack)
        stack.discard(id(obj))
        return
    if isinstance(obj, dict):
        _enter(obj, path, stack)
        for key, value in obj.items():
            if not isinstance(key, str):
                raise CanonicalJSONError(
                    f"{path}: dict 键必须是 str，收到 {type(key).__name__}"
                )
            _ensure_encodable(key, f"{path} 的键")
            _validate(value, path=f"{path}.{key}", stack=stack)
        stack.discard(id(obj))
        return
    # Decimal / bytes / set / 自定义对象等：不在 JSON 数据模型内，静默转换会制造第二套口径。
    raise CanonicalJSONError(f"{path}: 不支持的类型 {type(obj).__name__}")


def _enter(obj: list[object] | dict[str, object], path: str, stack: set[int]) -> None:
    if id(obj) in stack:
        raise CanonicalJSONError(f"{path}: 检测到循环引用")
    stack.add(id(obj))


def _ensure_encodable(text: str, path: str) -> None:
    """孤立代理项（surrogate）无法编码成 UTF-8；提前抛出可读错误。"""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonicalJSONError(f"{path}: 含无法编码为 UTF-8 的码元（孤立代理项）") from exc