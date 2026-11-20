"""字段抽取器（S1-5）：关键字锚定 + 方向/距离约束 + 归一化 + 冲突保留。

设计要点（都对应任务卡里列的坑）：
1. **规则显式**：锚点同义词、方向（right/below）、最大距离、允许的纵向区间、多候选取最大——
   全部写在 `templates/field_rules.yaml` 里，**不允许"取最后一行的金额"这种模糊实现**；
2. **不静默修正**：同一字段出现两个不同候选时，保留 `candidates` 并记 conflict，由前端让用户选；
3. **金额一律 Decimal**（`validators.parse_money`），日期归一化成 ISO；
4. **保留来源行号**：`source_line_no` 回指 `OcrLine.line_no`，前端要能 hover 高亮、审计要能回溯。

抽取顺序：锚点同行右侧 → 锚点下方/右侧邻近行 → 区域内的形态兜底（仅在规则显式给了 zone 时）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.ocr.layout import LayoutResult
from app.ocr.types import ExtractedField, ExtractionResult, OcrLine
from app.ocr.validators import parse_date, parse_money, parse_upper_amount, run_validators
# 置信度公式集中在 services/confidence.py（AGENTS §10.3 把"置信度聚合"列在 OCR 细则里），
# 抽取器只负责收集证据并调用它，不自己决定三档。
from app.services.confidence import apply_validator_cap, compute_field_confidence

__all__ = ["FieldRule", "load_field_rules", "rules_from_specs", "extract_fields"]

RULES_PATH = Path(__file__).resolve().parent / "templates" / "field_rules.yaml"
_ANCHOR_STRENGTH = {0: 1.0, 1: 0.92, 2: 0.84}      # 锚点顺序即强度：第一个最可信
_FUZZY_PENALTY = 0.85                               # 需要跨行取值时打折
_DEFAULT_MAX_DISTANCE = 300.0


@dataclass(frozen=True)
class FieldRule:
    name: str
    value_type: str
    anchors: list[str]
    direction: str = "right"
    regex: str | None = None
    max_distance: float = _DEFAULT_MAX_DISTANCE
    zone: tuple[float, float] | None = None
    occurrence: str = "all"     # all=收集前 3 个锚点行（用于发现冲突）；first/last=只取一个
    tickets: list[str] | None = None
    required: bool = False
    if_multiple: str | None = None
    value_index: int = 0
    exclude: list[str] | None = None    # 含这些词的行不参与锚定（如"项目名称"这种表头陷阱）
    whole_line_anchors: list[str] | None = None   # 命中这些锚点时整行即值（无标签的名称行）        # 一行内出现多个值时的取第几个（合计行的 金额/税额）
    rule_id: str = ""

    def applies_to(self, ticket_type: str) -> bool:
        if not self.tickets:
            return True
        return ticket_type in self.tickets


def load_field_rules(path: Path | None = None) -> list[FieldRule]:
    path = path or RULES_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules: list[FieldRule] = []
    for name, spec in (data.get("fields") or {}).items():
        zone = spec.get("zone")
        rules.append(FieldRule(
            name=name,
            value_type=spec.get("value_type", "text"),
            anchors=list(spec.get("anchors") or []),
            direction=spec.get("direction", "right"),
            regex=spec.get("regex"),
            max_distance=float(spec.get("max_distance", _DEFAULT_MAX_DISTANCE)),
            zone=(float(zone[0]), float(zone[1])) if zone else None,
            occurrence=spec.get("occurrence", "all"),
            tickets=spec.get("tickets"),
            required=bool(spec.get("required", False)),
            if_multiple=spec.get("if_multiple"),
            value_index=int(spec.get("value_index", 0)),
            exclude=list(spec.get("exclude") or []) or None,
            whole_line_anchors=list(spec.get("whole_line_anchors") or []) or None,
            rule_id=f"{name}:{spec.get('value_type', 'text')}",
        ))
    if not rules:
        raise ValueError(f"字段规则为空：{path}")
    return rules


# DB 的 `field_specs_json.strategy` → 抽取方向（S2-8 起的库内规则形态）
_STRATEGY_TO_DIRECTION = {
    "inline_right": "right",
    "below_center": "below",
    "table_column": "any",
    "label_neighbor": "any",
    "total_zone": "any",
}


def rules_from_specs(specs: dict[str, Any]) -> list[FieldRule]:
    """`ticket_templates.field_specs_json` → `FieldRule`（S3-1 流水线读库用）。

    与 `load_field_rules()` 必须逐字段等价（单测钉住）：DB 规格是 YAML 的 seed 快照，
    两条路给出不同规则就会让"有没有 seed 过库"悄悄改变识别结果。
    """
    rules: list[FieldRule] = []
    for name, spec in (specs or {}).items():
        if not isinstance(spec, dict):
            continue
        keywords = spec.get("keywords") or spec.get("anchors") or []
        direction = spec.get("direction") or _STRATEGY_TO_DIRECTION.get(
            str(spec.get("strategy") or ""), "right"
        )
        zone = spec.get("zone")
        rules.append(FieldRule(
            name=name,
            value_type=str(spec.get("value_type", "text")),
            anchors=[str(item) for item in keywords],
            direction=direction,
            regex=spec.get("regex") or spec.get("pattern"),
            max_distance=float(spec.get("max_distance") or _DEFAULT_MAX_DISTANCE),
            zone=(float(zone[0]), float(zone[1])) if zone else None,
            occurrence=str(spec.get("occurrence", "all")),
            tickets=spec.get("tickets"),
            required=bool(spec.get("required", False)),
            if_multiple=spec.get("if_multiple"),
            value_index=int(spec.get("value_index", 0)),
            exclude=spec.get("exclude"),
            whole_line_anchors=spec.get("whole_line_anchors"),
            rule_id=f"{name}:{spec.get('value_type', 'text')}",
        ))
    return rules


# ---------------------------------------------------------------------------
# 值归一化
# ---------------------------------------------------------------------------
def _clean_text(text: str) -> str:
    return re.sub(r"^[\s:：,，、.·\-—_]+", "", text).strip()


def _normalize(value_type: str, raw: str) -> str | None:
    """把切出来的原始文本归一化成契约值；归一化不出来就返回 None（宁可缺，不要脏值）。"""
    text = _clean_text(raw)
    if not text:
        return None
    if value_type == "money":
        amount = parse_money(text)
        return f"{amount:.2f}" if amount is not None else None
    if value_type == "upper_money":
        amount = parse_upper_amount(text)
        return f"{amount:.2f}" if amount is not None else None
    if value_type == "date":
        parsed = parse_date(text)
        return parsed.isoformat() if parsed else None
    if value_type == "code":
        # 纯数字编号：先把常见 OCR 形近字母纠回来，再剔非数字（否则 O 被整颗删掉会短一位）
        digits = re.sub(r"\D", "", text.translate(_CODE_CONFUSIONS))
        return digits or None
    if value_type == "tax_no":
        # 统一社会信用代码不含 I/O（GB 32100），OCR 常把 0 读成 O、1 读成 I；只纠这两个安全映射
        cleaned = re.sub(r"[\s\-]", "", text).upper().translate(_TAX_NO_CONFUSIONS)
        return cleaned or None
    if value_type == "person":
        match = re.search(r"[\u4e00-\u9fa5]{2,4}", text)
        return match.group(0) if match else None
    # text：去掉行尾噪声（数量/单位等），最多保留 40 字
    return text[:40] or None


def _compact(text: str) -> str:
    """去掉 OCR 插在字间的空格：实测"金 额""铁 路"这种逐字空格非常常见。"""
    return text.replace(" ", "").replace("\u3000", "")


def _tail_after_anchor(text: str, anchor: str) -> str:
    compact = _compact(text)
    index = compact.find(anchor)
    if index < 0:
        return ""
    return compact[index + len(anchor):]


def _display_raw(value_type: str, raw: str, normalized: str) -> str:
    """`field_value` 存"票面原文"；但 code/tax_no 的 raw 可能带着 OCR 形近字（O/0），
    纠正后的值才是票面真实内容，raw_text 仍保留机器原样供审计。"""
    return normalized if value_type in {"code", "tax_no"} else raw


def _extract_matched(rule: FieldRule, text: str, anchor: str) -> tuple[str, str] | None:
    """返回 (票面原文, 归一值)；归一不出来返回 None。

    为什么要同时给两个：`invoice_fields.field_value` 契约要求"票面原文形态"，而抽取与校验都吃归一值。
    以前只返回归一值，原文在函数内部就丢了（S3-4/D-062）。
    """
    if rule.whole_line_anchors and anchor in rule.whole_line_anchors:
        raw = _clean_text(_compact(text))
        normalized = _normalize(rule.value_type, raw)
        return (_display_raw(rule.value_type, raw, normalized), normalized) if normalized else None
    tail = _clean_text(_tail_after_anchor(text, anchor))
    if not tail:
        return None
    if rule.regex:
        # 用 finditer + group(0)：DB 里的正则可能带捕获组，findall 会返回 tuple（丢整数部分）
        matches = [match.group(0) for match in re.finditer(rule.regex, tail)]
        if not matches:
            matches = [match.group(0) for match in re.finditer(rule.regex, text)]
        if not matches:
            return None
        # 一行多个数值时按 value_index 取（合计行常是"金额 税额"两列）
        if rule.value_index and len(matches) > rule.value_index:
            raw = matches[rule.value_index]
        else:
            raw = matches[0]
        normalized = _normalize(rule.value_type, raw)
        return (_display_raw(rule.value_type, raw, normalized), normalized) if normalized else None
    normalized = _normalize(rule.value_type, tail)
    return (_display_raw(rule.value_type, tail, normalized), normalized) if normalized else None


def _extract_value(rule: FieldRule, text: str, anchor: str) -> str | None:
    """兼容包装：只要归一值。"""
    matched = _extract_matched(rule, text, anchor)
    return matched[1] if matched is not None else None


def _in_zone(rule: FieldRule, line: OcrLine, image_height: int) -> bool:
    if not rule.zone or not image_height:
        return True
    relative = line.y_center / float(image_height)
    return rule.zone[0] <= relative <= rule.zone[1]


def _neighbour_value(rule: FieldRule, anchor_line: OcrLine, lines: list[OcrLine],
                     image_height: int) -> tuple[str, str, int] | None:
    """锚点同行取不到值时，在允许的方向/距离内找邻近行。"""
    if rule.direction == "right":
        candidates = [line for line in lines
                      if abs(line.y_center - anchor_line.y_center) <= 30
                      and line.x_left > anchor_line.x_left]
        candidates.sort(key=lambda line: line.x_left)
    elif rule.direction == "below":
        candidates = [line for line in lines
                      if line.y_center > anchor_line.y_center
                      and line.y_center - anchor_line.y_center <= rule.max_distance]
        candidates.sort(key=lambda line: line.y_center)
    else:   # any：优先同行右侧，其次下方
        right = [line for line in lines
                 if abs(line.y_center - anchor_line.y_center) <= 30 and line.x_left > anchor_line.x_left]
        below = [line for line in lines if line.y_center > anchor_line.y_center]
        candidates = sorted(right, key=lambda line: line.x_left) + \
                     sorted(below, key=lambda line: line.y_center)

    collected: list[tuple[str, str, int]] = []
    for line in candidates:
        if not _in_zone(rule, line, image_height):
            continue
        # 距离按"方向"度量：右取水平间距、下取垂直间距。
        # 用曼哈顿距离会把"合计"与其右侧右对齐的金额（水平差 800px）判成过远，
        # 实测那样根本取不到值，只能退到低优先级锚点。
        horizontal = abs(line.x_left - anchor_line.x_left)
        vertical = abs(line.y_center - anchor_line.y_center)
        if rule.direction == "right":
            distance = horizontal
        elif rule.direction == "below":
            distance = vertical
        else:
            distance = min(horizontal, vertical)
        if distance > rule.max_distance:
            continue
        if rule.regex:
            matches = re.findall(rule.regex, line.text)
            if not matches:
                continue
            if rule.value_index and len(matches) > rule.value_index:
                raw = matches[rule.value_index]
                normalized = _normalize(rule.value_type, raw)
                if normalized:
                    return _display_raw(rule.value_type, raw, normalized), normalized, line.line_no
            # value_index 指向的行内没有第 N 个值 → 先把本行的值收起来，继续往下找
            for raw in matches:
                normalized = _normalize(rule.value_type, raw)
                if normalized:
                    collected.append(
                        (_display_raw(rule.value_type, raw, normalized), normalized, line.line_no)
                    )
                if len(collected) > rule.value_index:
                    return collected[rule.value_index]
        else:
            raw = _clean_text(line.text)
            normalized = _normalize(rule.value_type, raw)
            if normalized:
                return _display_raw(rule.value_type, raw, normalized), normalized, line.line_no
    return None


# OCR 形近字纠偏：只用于"本应是数字"的字段，避免把合法字母改坏
_TAX_NO_CONFUSIONS = str.maketrans({"O": "0", "I": "1"})
_CODE_CONFUSIONS = str.maketrans(
    {"O": "0", "o": "0", "I": "1", "i": "1", "l": "1", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2"}
)

_MONEY_PATTERN = re.compile(r"[¥￥]?\s*-?\d[\d,]*\.\d{2}")


def _money(field: ExtractedField | None) -> "Decimal | None":
    """取字段的 Decimal 金额（字段不存在或不是金额时返回 None）。"""
    if field is None:
        return None
    return parse_money(field.value)


def _infer_amount_tax(lines: list[OcrLine], total_line: OcrLine, total_value: Decimal,
                      image_height: int) -> tuple[Decimal, Decimal] | None:
    """在"价税合计"附近找一对金额，使其和等于合计值。

    为什么需要这一步：旧版票的**竖屏截图没有"合计"这一行**（只有"价税合计（大写）"），
    金额与税额散在相邻几行里，纯锚点取不到（实测 10 张 vat 样本全漏）。
    这不是"猜"，是用 `amount + tax = total` 这条硬约束反解，并额外要求：
      ① 两个值落在同一纵向窗口（±5% 图高）；
      ② 和与合计值相差 ≤ 0.01；
      ③ 较大的当金额、较小的当税额（税额不会大于金额）。
    解不唯一就直接返回 None——宁可缺失，也不硬凑一个值。
    """
    if not lines or image_height <= 0:
        return None
    window = max(int(image_height * 0.05), 40)
    candidates: list[Decimal] = []
    for line in lines:
        if abs(line.y_center - total_line.y_center) > window:
            continue
        for raw in _MONEY_PATTERN.findall(line.text):
            amount = parse_money(raw)
            if amount is None or abs(amount - total_value) <= Decimal("0.01"):
                continue
            candidates.append(amount)
    unique = sorted(set(candidates))
    pairs = [(a, b) for index, a in enumerate(unique) for b in unique[index + 1:]
             if abs(a + b - total_value) <= Decimal("0.01")]
    if len(pairs) != 1:
        return None
    left, right = pairs[0]
    return max(left, right), min(left, right)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def extract_fields(lines: list[OcrLine], image_size: tuple[int, int],
                   ticket_type: str = "unknown",
                   layout: LayoutResult | None = None,
                   rules: list[FieldRule] | None = None) -> ExtractionResult:
    """从文本行抽出字段。不抛异常：抽不出来就是"缺"，由调用方决定怎么处理。"""
    rules = rules or load_field_rules()
    _, image_height = image_size
    ordered = sorted(lines, key=lambda line: (line.y_center, line.x_left))
    result = ExtractionResult(ticket_type=ticket_type)

    for rule in rules:
        if not rule.applies_to(ticket_type):
            continue
        hits: list[ExtractedField] = []

        for anchor_rank, anchor in enumerate(rule.anchors):
            anchor_lines = [line for line in ordered
                            if anchor in _compact(line.text)
                            and not any(word in _compact(line.text) for word in (rule.exclude or []))]
            if not anchor_lines:
                continue
            if rule.occurrence == "last":
                anchor_lines = list(reversed(anchor_lines))
            # occurrence 指定的规则（如"第一/最后一个纳税人识别号"）只取一个锚点行，
            # 否则会把买方/卖方两个税号都收进来，制造假冲突。
            selected = anchor_lines[:1] if rule.occurrence in {"first", "last"} else anchor_lines[:3]
            for anchor_line in selected:
                base_strength = _ANCHOR_STRENGTH.get(anchor_rank, 0.8)
                matched = _extract_matched(rule, anchor_line.text, anchor)
                source_line = anchor_line
                strength = base_strength
                used_neighbour = False
                if matched is None:
                    neighbour = _neighbour_value(rule, anchor_line, ordered, image_height)
                    if neighbour is None:
                        continue
                    raw_value, value, source_line_no = neighbour
                    source_line = next((line for line in ordered if line.line_no == source_line_no), anchor_line)
                    strength = base_strength * _FUZZY_PENALTY
                    used_neighbour = True
                else:
                    raw_value, value = matched
                agreement = _FUZZY_PENALTY if used_neighbour else 1.0
                hits.append(ExtractedField(
                    name=rule.name,
                    value=value,
                    raw_value=raw_value,
                    raw_text=source_line.text,
                    source_line_no=source_line.line_no,
                    confidence=compute_field_confidence(
                        anchor_line.score, source_line.score,
                        agreement=agreement, rule_factor=base_strength,
                    ),
                    rule_id=rule.rule_id,
                    rule_strength=round(strength, 3),
                    bbox=source_line.bbox(),
                    anchor_score=anchor_line.score,
                    value_score=source_line.score,
                    agreement=agreement,
                ))
            if hits:
                break      # 高优先级锚点命中就不再往低优先级找

        if not hits:
            if rule.required:
                result.missing_required.append(rule.name)
                result.warnings.append(f"missing_required:{rule.name}")
            continue

        # 先用"锚点优先级"消解：高优先级锚点的结果直接胜出。
        # 只有**同优先级证据互相矛盾**时才叫冲突——否则每条规则都会因为表头/明细里
        # 同名列而报一堆假冲突，前端会被噪声淹没。
        best_strength = max(hit.rule_strength for hit in hits)
        top_hits = [hit for hit in hits if hit.rule_strength == best_strength]
        distinct = []
        for hit in top_hits:
            if hit.value not in distinct:
                distinct.append(hit.value)

        if len(distinct) == 1:
            result.fields[rule.name] = top_hits[0]
            continue

        # 多个不同候选：按规则显式处理（取最大），但**保留全部候选**供前端选择，绝不静默丢
        if rule.if_multiple == "max":
            numeric = []
            for hit in top_hits:
                amount = parse_money(hit.value)
                if amount is not None:
                    numeric.append((amount, hit))
            if numeric:
                best = max(numeric, key=lambda item: item[0])[1]
                best.candidates = [hit.value for hit in top_hits]
                result.fields[rule.name] = best
                continue
        result.fields[rule.name] = top_hits[0]
        result.fields[rule.name].candidates = [hit.value for hit in top_hits]
        result.conflicts.append({
            "field": rule.name,
            "candidates": [{"value": hit.value, "raw_text": hit.raw_text,
                            "source_line_no": hit.source_line_no, "confidence": hit.confidence}
                           for hit in top_hits],
            "note": "同优先级证据给出不同取值，未自动替换，请人工确认",
        })

    # 金额/税额缺失、但合计值在：用"邻近两个金额之和等于合计"反解（仅唯一解时采用）
    if ticket_type in {"vat_digital", "vat_general"}:
        total_field = result.fields.get("total_amount")
        total_value = _money(total_field)
        if (total_value is not None and total_field is not None
                and total_field.source_line_no is not None
                and any(name not in result.fields for name in ("amount", "tax_amount"))):
            total_line = next((line for line in ordered if line.line_no == total_field.source_line_no), None)
            if total_line is not None:
                pair = _infer_amount_tax(ordered, total_line, total_value, image_height)
                if pair is not None:
                    amount_value, tax_value = pair
                    for name, value in (("amount", amount_value), ("tax_amount", tax_value)):
                        if name in result.fields:
                            continue
                        result.fields[name] = ExtractedField(
                            name=name, value=f"{value:.2f}", raw_text="（按合计关系在邻近行配对）",
                            source_line_no=total_line.line_no, confidence=0.6,
                            rule_id=f"paired:sum:{name}", rule_strength=0.0, bbox=None,
                        )
                        result.warnings.append(f"paired:{name}")

    # 金额三件套互相补：只有两个抽到时，用 total = amount + tax 补第三个。
    # 这是**显式推导**不是静默修正：rule_id=derived:*、置信度降档、并留 warning，
    # 前端会把这类值标成"推导值"，用户可以直接改。
    if ticket_type in {"vat_digital", "vat_general"}:
        total = _money(result.fields.get("total_amount"))
        amount = _money(result.fields.get("amount"))
        tax = _money(result.fields.get("tax_amount"))
        derived_candidates = (
            ("amount", total - tax if (total is not None and tax is not None and amount is None) else None),
            ("tax_amount", total - amount if (total is not None and amount is not None and tax is None) else None),
            ("total_amount", amount + tax if (amount is not None and tax is not None and total is None) else None),
        )
        for name, derived in derived_candidates:
            if derived is None:
                continue
            result.fields[name] = ExtractedField(
                name=name, value=f"{derived:.2f}", raw_text="（由合计关系推导）",
                source_line_no=None, confidence=0.55, rule_id=f"derived:{name}",
                rule_strength=0.0, bbox=None,
            )
            result.warnings.append(f"derived:{name}")

    # 校验：失败把该字段置信度折半（任务卡要求），errors 全量返回
    validation = run_validators(result.fields, ticket_type)
    for issue in validation.issues:
        if issue.severity == "error" and issue.field in result.fields:
            field = result.fields[issue.field]
            # 校验判错 = 这条值不可信，直接压进"必须人工"区（任务卡要求 <0.5）
            field.confidence = apply_validator_cap(field.confidence)
    result.warnings.extend(f"{issue.field}:{issue.code}" for issue in validation.issues)
    return result