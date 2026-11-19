"""票种识别与版面区域划分（S1-4）。

两件事：
1. **判票种**：模板里的关键字加权打分（标题行权重 ×3）+ 长宽比先验 + "互斥关键字"扣分。
   分数不够或与第二名太近就返回 `unknown`——**宁可 unknown，也不要猜错**（猜错会让 S1-5 用错规则）。
2. **分区域**：按模板里的**归一化矩形**把每一行分到 header / body / footer，并做行聚类得到 block。
   归一化坐标是硬要求：写死像素值换分辨率就废（任务卡明确列的坑）。

为什么不上深度学习分类器：4 类票种、标题关键字几乎 100% 命中，规则可解释、零标注成本、可维护；
40–60 张训练集上训分类器必过拟合，答辩也解释不清"为什么用 CNN 判 4 个类"。这条记在 D-028。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from app.ocr.types import OcrLine

__all__ = [
    "TicketTemplate", "Classification", "RegionAssignment", "LayoutResult",
    "load_templates", "classify_ticket", "assign_regions", "analyze_layout",
]

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_TEMPLATE_THRESHOLD = 0.25      # 归一化得分下限
MIN_MARGIN = 0.08                      # 第一名与第二名的最小差距


# ---------------------------------------------------------------------------
# 模板
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TicketTemplate:
    type_code: str
    type_name: str
    keywords: list[dict]
    exclusive_keywords: list[dict]
    regions: dict[str, list[float]]
    regions_portrait: dict[str, list[float]] | None
    aspect_ratio: tuple[float, float]

    @property
    def max_score(self) -> float:
        return sum(float(k["weight"]) for k in self.keywords) or 1.0

    def regions_for(self, aspect: float) -> dict[str, list[float]]:
        """竖版（宽高比 < 1）且有竖版区域时用竖版那套。"""
        if self.regions_portrait and aspect < 1.0:
            return self.regions_portrait
        return self.regions


def _parse_template(path: Path) -> TicketTemplate:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    ratio = data.get("aspect_ratio") or [0.0, 99.0]
    return TicketTemplate(
        type_code=data["type_code"],
        type_name=data["type_name"],
        keywords=list(data.get("keywords") or []),
        exclusive_keywords=list(data.get("exclusive_keywords") or []),
        regions=data["regions"],
        regions_portrait=data.get("regions_portrait"),
        aspect_ratio=(float(ratio[0]), float(ratio[1])),
    )


def load_templates(directory: Path | None = None) -> dict[str, TicketTemplate]:
    directory = directory or TEMPLATE_DIR
    templates: dict[str, TicketTemplate] = {}
    for path in sorted(directory.glob("*.yaml")):
        # templates/ 目录里还放着 field_rules.yaml（字段抽取规则），它们没有 type_code，
        # 不能当票种模板解析 —— 这里按"是否声明 type_code"过滤。
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "type_code" not in data:
            continue
        template = _parse_template(path)
        templates[template.type_code] = template
    if not templates:
        raise FileNotFoundError(f"没有找到票种模板：{directory}")
    return templates


# ---------------------------------------------------------------------------
# 票种识别
# ---------------------------------------------------------------------------
@dataclass
class Classification:
    ticket_type: str
    score: float
    template_name: str
    matched: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_known(self) -> bool:
        return self.ticket_type != "unknown"


def _title_zone_cut(image_size: tuple[int, int]) -> float:
    """标题区下沿（归一化 y）。票据标题几乎都在最上面，给 15% 足够宽容。"""
    return image_size[1] * 0.15


def classify_ticket(lines: list[OcrLine], image_size: tuple[int, int],
                    templates: dict[str, TicketTemplate] | None = None
                    ) -> Classification:
    """按关键字加权 + 长宽比先验判票种。

    标题行（图像上部 15%）里命中的关键字权重 ×3：标题是最强的版式信号，
    正文里"普通发票"这种词可能出现在备注里，不能同等对待。
    """
    templates = templates or load_templates()
    width, height = image_size
    aspect = width / height if height else 0.0
    title_cut = _title_zone_cut(image_size)
    # 标题常被检测切成 2–3 段，逐行子串匹配会漏；把标题区所有行拼成一条再匹配一次
    title_text = "".join(line.text for line in lines if line.y_center <= title_cut).replace(" ", "")

    raw_scores: dict[str, float] = {}
    matched_map: dict[str, list[str]] = {}
    for code, template in templates.items():
        score = 0.0
        matched: list[str] = []
        for keyword in template.keywords:
            text = str(keyword["text"])
            weight = float(keyword["weight"])
            hits = [line for line in lines if text in line.text.replace(" ", "")]
            in_title = bool(hits and any(line.y_center <= title_cut for line in hits))
            if not hits and text in title_text:
                in_title = True      # 标题跨行拼接后命中
                matched.append(text)
                score += weight * 3.0
                continue
            if not hits:
                continue
            score += weight * (3.0 if in_title else 1.0)
            matched.append(text)
        for keyword in template.exclusive_keywords:
            text = str(keyword["text"])
            if any(text in line.text for line in lines):
                score -= float(keyword["weight"])
        if template.aspect_ratio[0] <= aspect <= template.aspect_ratio[1]:
            score += 1.0
        raw_scores[code] = score
        matched_map[code] = matched

    if not raw_scores:
        return Classification("unknown", 0.0, "", [], {}, ["no_template"])

    normalized = {code: max(0.0, value) / templates[code].max_score * 3.0
                  for code, value in raw_scores.items()}
    # 归一化：命中全部关键字（且都在标题区）≈ 3.0；阈值 0.25 ≈ 命中一个小权重关键字
    best_code = max(normalized, key=normalized.get)
    best_score = normalized[best_code]
    ranked = sorted(normalized.values(), reverse=True)
    margin = ranked[0] - ranked[1] if len(ranked) > 1 else ranked[0]

    warnings: list[str] = []
    if best_score < DEFAULT_TEMPLATE_THRESHOLD:
        warnings.append(f"score_too_low:{best_score:.2f}")
        return Classification("unknown", round(best_score, 3), "", matched_map.get(best_code, []),
                              normalized, warnings)
    if margin < MIN_MARGIN:
        warnings.append(f"ambiguous:{margin:.2f}")
        return Classification("unknown", round(best_score, 3), "", matched_map.get(best_code, []),
                              normalized, warnings)

    template = templates[best_code]
    return Classification(best_code, round(min(best_score, 3.0), 3), template.type_name,
                          matched_map.get(best_code, []), normalized, warnings)


# ---------------------------------------------------------------------------
# 区域划分
# ---------------------------------------------------------------------------
@dataclass
class TextBlock:
    block_id: int
    region: str
    line_nos: list[int]
    bbox: tuple[int, int, int, int]
    text: str


@dataclass
class RegionAssignment:
    line_regions: dict[int, str]           # line_no → header/body/footer
    blocks: list[TextBlock]
    region_counts: dict[str, int]
    warnings: list[str] = field(default_factory=list)


def _content_box(lines: list[OcrLine], image_size: tuple[int, int]) -> tuple[float, float]:
    """所有文本行的纵向范围（内容框），用作归一化基准。

    为什么不用整图高度：透视校正可能把一点桌面框进来（实测底部多了 ~13%），
    纸面内容整体上移，写死在 0.88 的 footer 边界就永远落空。按"内容框"归一化对这类偏移免疫。
    """
    height = image_size[1] or 1
    if not lines:
        return 0.0, float(height)
    top = min(min(point[1] for point in line.box) for line in lines)
    bottom = max(max(point[1] for point in line.box) for line in lines)
    if bottom - top < 10:      # 内容挤成一条线时退回整图，避免除零
        return 0.0, float(height)
    return float(top), float(bottom)


def _region_of(line: OcrLine, regions: dict[str, list[float]], image_size: tuple[int, int],
               content_box: tuple[float, float] | None = None) -> str:
    width, height = image_size
    top, bottom = content_box or (0.0, float(height or 1))
    span = max(bottom - top, 1.0)
    x = min(max(line.x_left / width if width else 0.0, 0.0), 1.0)
    y = min(max((line.y_center - top) / span, 0.0), 1.0)
    for name in ("header", "body", "footer"):
        rect = regions.get(name)
        if not rect:
            continue
        x1, y1, x2, y2 = rect
        if x1 <= x <= x2 and y1 <= y <= y2:
            return name
    return "body"   # 落在缝隙里的一律归 body：宁可粗一点，也不要丢行


def _cluster_blocks(lines: list[OcrLine], line_regions: dict[int, str]) -> list[TextBlock]:
    """把相邻行聚成块：垂直间距小于 1.5 倍行高、且水平有重叠 → 同块。"""
    if not lines:
        return []
    ordered = sorted(lines, key=lambda line: (line.y_center, line.x_left))
    blocks: list[TextBlock] = []
    current: list[OcrLine] = [ordered[0]]
    for line in ordered[1:]:
        previous = current[-1]
        gap = line.y_center - previous.y_center
        threshold = max(previous.height, line.height) * 1.5
        same_region = line_regions.get(line.line_no) == line_regions.get(previous.line_no)
        if gap <= threshold and same_region:
            current.append(line)
        else:
            blocks.append(_make_block(len(blocks), current, line_regions))
            current = [line]
    blocks.append(_make_block(len(blocks), current, line_regions))
    return blocks


def _make_block(block_id: int, lines: list[OcrLine], line_regions: dict[int, str]) -> TextBlock:
    xs = [point[0] for line in lines for point in line.box]
    ys = [point[1] for line in lines for point in line.box]
    return TextBlock(
        block_id=block_id,
        region=line_regions.get(lines[0].line_no, "body"),
        line_nos=[line.line_no for line in lines],
        bbox=(int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))),
        text="\n".join(line.text for line in lines),
    )


def assign_regions(lines: list[OcrLine], image_size: tuple[int, int],
                   classification: Classification | None = None,
                   templates: dict[str, TicketTemplate] | None = None) -> RegionAssignment:
    if classification is None:
        classification = classify_ticket(lines, image_size, templates)

    warnings: list[str] = []
    if classification.is_known:
        templates = templates or load_templates()
        template = templates[classification.ticket_type]
        width, height = image_size
        regions = template.regions_for(width / height if height else 1.0)
    else:
        # 未知票种：全部归 body，下游 extractor 走通用规则（任务卡要求这么降级）
        regions = {"header": [0.0, 0.0, 1.0, 0.0], "body": [0.0, 0.0, 1.0, 1.0],
                   "footer": [0.0, 1.0, 1.0, 1.0]}
        warnings.append("unknown_ticket:全部归 body，走通用抽取")

    content_box = _content_box(lines, image_size)
    line_regions = {line.line_no: _region_of(line, regions, image_size, content_box) for line in lines}
    counts: dict[str, int] = {}
    for region in line_regions.values():
        counts[region] = counts.get(region, 0) + 1
    if lines and counts.get("header", 0) == 0 and classification.is_known:
        warnings.append("empty_header:模板 header 区没有落任何行，检查区域矩形或预处理")

    return RegionAssignment(line_regions=line_regions,
                            blocks=_cluster_blocks(lines, line_regions),
                            region_counts=counts, warnings=warnings)


@dataclass
class LayoutResult:
    classification: Classification
    regions: RegionAssignment

    @property
    def ticket_type(self) -> str:
        return self.classification.ticket_type


def analyze_layout(lines: list[OcrLine], image_size: tuple[int, int],
                   templates: dict[str, TicketTemplate] | None = None) -> LayoutResult:
    """一步到位：判票种 + 分区域。S1-5 与 S3 流水线都调这个入口。"""
    templates = templates or load_templates()
    classification = classify_ticket(lines, image_size, templates)
    regions = assign_regions(lines, image_size, classification, templates)
    return LayoutResult(classification=classification, regions=regions)