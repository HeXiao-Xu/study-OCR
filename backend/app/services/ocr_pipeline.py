"""OCR 流水线（S3-1）：下载 → 渲染/预处理 → 检测识别 → 票种 → 抽取 → 校验 → 落库。

为什么单独一个 service 而不是继续写在 `tasks/ocr_task.py`：
- ARQ 只是宿主，流水线要能被脚本、测试、将来的批量接口复用；
- 依赖（引擎/存储/分页输入/模板/时钟）全部可注入，测试不需要加载 PaddleOCR 或连 MinIO；
- `job_service` 只管状态机与终态守卫，谁也不该知道对方内部。

事务边界（SCHEMA §3.5 的受控例外，D-055）：
- `ocr_jobs.stage/metrics` 与 `invoices.ocr_status='running'` 中途提交，让轮询看得见阶段；
- `ocr_lines`/`invoice_fields`/`invoices` 其余列只在最后一次事务提交，失败整批回滚，
  不会出现"识别失败但字段半截落库"。

失败分类沿用 `job_service.is_retryable`：网络/临时 IO → 回 `pending` 重试；
参数/契约/数据问题 → 终态 `failed`。`error` 只落错误类型与短消息，不含票面明文。
"""
from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ValidationFailedError
from app.models.enums import FieldSource, InvoiceStatus, OcrStage, OcrStatus
from app.models.field_rules import FieldRule
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.ocr_lines import OcrLine as OcrLineRow
from app.ocr.detector import OcrOptions, get_engine
from app.ocr.extractor import (
    FieldRule as ExtractorRule,
    extract_fields,
    load_field_rules,
    rules_from_specs,
)
from app.ocr.intake import PreparedPage, prepare_pages
from app.ocr.layout import (
    Classification,
    LayoutResult,
    RegionAssignment,
    assign_regions,
    classify_ticket,
    load_templates,
)
from app.ocr.types import ExtractedField, ExtractionResult, OcrLine
from app.ocr.validators import parse_date, parse_money, run_validators
from app.services import template_registry
from app.services.job_service import AbortAttempt
from app.services.storage import StorageService, get_storage

__all__ = [
    "MergedField",
    "NEED_REVIEW_THRESHOLD",
    "PIPELINE_STAGES",
    "PipelineDeps",
    "RecognizedPage",
    "TEMP_PREFIX",
    "field_rows",
    "invoice_values",
    "line_rows",
    "merge_page_fields",
    "run_pipeline",
]

PIPELINE_STAGES: tuple[str, ...] = (
    OcrStage.PREPROCESS.value,
    OcrStage.DETECT.value,
    OcrStage.RECOGNIZE.value,
    OcrStage.EXTRACT.value,
)
TEMP_PREFIX = "ocr-pipeline-"
# SCHEMA §3.3 既定阈值（方案 3.4 规则 1）；S3-2 会把聚合公式与阈值搬进配置
NEED_REVIEW_THRESHOLD = 0.85
# warnings 只是给人看的定位线索，不能无限增长（JSONB 也会一直变大）
_WARNING_LIMIT = 20
_MONEY_COLUMNS = ("amount", "tax_amount", "total_amount")
_TEXT_COLUMNS = (
    "invoice_code",
    "invoice_no",
    "buyer_name",
    "buyer_tax_no",
    "seller_name",
    "seller_tax_no",
    "check_code",
)
_STATUS_OPEN = (OcrStatus.PENDING.value, OcrStatus.RUNNING.value)


@dataclass(frozen=True)
class MergedField:
    """多页合并后的字段：值 + 命中页（`invoice_fields.source_page_no` 用它）。"""

    field: ExtractedField
    page_no: int


@dataclass
class RecognizedPage:
    page_no: int
    lines: list[OcrLine]
    image_size: tuple[int, int]
    prepared: PreparedPage
    assignment: RegionAssignment | None = None
    blocks: dict[int, str] = field(default_factory=dict)


@dataclass
class PipelineDeps:
    """可注入依赖：默认全部走真实实现，测试传假对象。"""

    engine: object | None = None
    storage: object | None = None
    prepare_pages: Callable[..., list[PreparedPage]] | None = None
    templates: dict[str, Any] | None = None
    registry: object | None = None
    clock: Callable[[], float] = time.monotonic


# --------------------------------------------------------------------------- #
# 纯函数（可单测，不碰 DB）
# --------------------------------------------------------------------------- #
def merge_page_fields(
    results: list[tuple[int, ExtractionResult]],
) -> tuple[dict[str, MergedField], list[str]]:
    """多页合并：**首页优先**，后续页只补空字段；同名字段不同值记 warning 但**不覆盖**。

    "后面一页写了不同的合计金额"是要人看的信号，不是该静默选一个的信号。
    """
    merged: dict[str, MergedField] = {}
    warnings: list[str] = []
    for page_no, result in sorted(results, key=lambda item: item[0]):
        for name, extracted in result.fields.items():
            existing = merged.get(name)
            if existing is None:
                merged[name] = MergedField(field=extracted, page_no=page_no)
            elif existing.field.value != extracted.value:
                warnings.append(f"page_conflict:{name}")
    return merged, warnings


def invoice_values(
    merged: dict[str, MergedField], *, ticket_type: str, template_id: int | None
) -> dict[str, object]:
    """字段 → `invoices` 冗余列。金额/日期转成库内类型；转不出来就不写（宁可缺，不要脏）。"""
    values: dict[str, object] = {"invoice_type": ticket_type, "template_id": template_id}
    for name in _MONEY_COLUMNS:
        item = merged.get(name)
        amount = parse_money(item.field.value) if item is not None else None
        if amount is not None:
            values[name] = amount
    date_item = merged.get("invoice_date")
    parsed = parse_date(date_item.field.value) if date_item is not None else None
    if parsed is not None:
        values["invoice_date"] = parsed
    for name in _TEXT_COLUMNS:
        item = merged.get(name)
        if item is not None and item.field.value:
            values[name] = item.field.value
    return values


def line_rows(
    pages: list[RecognizedPage], *, invoice_id: int, job_id: int
) -> list[dict[str, Any]]:
    """`ocr_lines` 行：四点坐标原样保留，block_id 形如 `b{页}-{块}`（SCHEMA 定稿 #9）。"""
    rows: list[dict[str, Any]] = []
    for page in pages:
        for line in page.lines:
            rows.append(
                {
                    "invoice_id": invoice_id,
                    "job_id": job_id,
                    "page_no": page.page_no,
                    "line_no": line.line_no,
                    "text": line.text,
                    "score": Decimal(str(round(float(line.score), 3))),
                    "box_json": [[float(point[0]), float(point[1])] for point in line.box],
                    "block_id": page.blocks.get(line.line_no),
                }
            )
    return rows


def field_rows(
    merged: dict[str, MergedField],
    *,
    invoice_id: int,
    rule_ids: dict[str, int],
    failed_fields: set[str],
) -> list[dict[str, Any]]:
    """`invoice_fields` 行：冲突两个值都留，低置信/校验失败标 need_review。"""
    rows: list[dict[str, Any]] = []
    for name, item in merged.items():
        extracted = item.field
        has_conflict = len(extracted.candidates) > 1
        rows.append(
            {
                "invoice_id": invoice_id,
                "field_name": name,
                "field_value": extracted.value,
                "normalized_value": extracted.value,
                "raw_text": extracted.raw_text,
                "confidence": Decimal(str(round(float(extracted.confidence), 3))),
                "bbox_json": list(extracted.bbox) if extracted.bbox is not None else None,
                "source": FieldSource.OCR.value,
                "source_page_no": item.page_no,
                "source_line_no": extracted.source_line_no,
                "rule_id": rule_ids.get(name),
                "need_review": (
                    float(extracted.confidence) < NEED_REVIEW_THRESHOLD or name in failed_fields
                ),
                "conflict": has_conflict,
                "conflict_value": extracted.candidates[1] if has_conflict else None,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
def _new_metrics(*, attempt: int) -> dict[str, Any]:
    return {
        "version": 1,
        "attempt": attempt,
        "pages_total": 0,
        "pages_ocr": 0,
        "stages": [],
        "timings_ms": {
            "download": 0,
            "intake": 0,
            "ocr": 0,
            "layout": 0,
            "extract": 0,
            "persist": 0,
            "total": 0,
        },
        "counts": {"lines": 0, "fields": 0, "missing_required": [], "conflicts": 0},
        "engine": {},
        "rules_source": "",
        "template_id": None,
        "quality": {"flag": None, "needs_retake": False, "retake_hints": [], "reasons": []},
        "warnings": [],
        "duplicate_invoice_no": False,
    }


def _complete(metrics: dict[str, Any], stage: str, elapsed_ms: float | None,
              note: str | None = None) -> None:
    entry: dict[str, Any] = {
        "stage": stage,
        "status": OcrStatus.SUCCEEDED.value,
        "elapsed_ms": None if elapsed_ms is None else int(round(elapsed_ms)),
    }
    if note:
        entry["note"] = note
    metrics["stages"].append(entry)


def _fail_stage(metrics: dict[str, Any], stage: str) -> None:
    metrics["stages"].append(
        {"stage": stage, "status": OcrStatus.FAILED.value, "elapsed_ms": None}
    )


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def _cap_warnings(warnings: list[str], limit: int = _WARNING_LIMIT) -> list[str]:
    # 去重且保序：同一字段在多页重复触发同一条 warning 时不该把列表撑爆
    return list(dict.fromkeys(warnings))[:limit]


def _check_deadline(deadline: float, clock: Callable[[], float]) -> None:
    if clock() > deadline:
        # TimeoutError 在 is_retryable 里：CPU 抖动值得再试一次
        raise TimeoutError("OCR 流水线超过时间预算")


# --------------------------------------------------------------------------- #
# 各阶段
# --------------------------------------------------------------------------- #
def _load_invoice(db: Session, job: OcrJob) -> tuple[Invoice, InvoiceFile]:
    invoice = db.get(Invoice, job.invoice_id)
    if invoice is None or invoice.status == InvoiceStatus.VOID.value:
        raise ValidationFailedError("票据不存在或已作废，不进入识别")
    file = db.get(InvoiceFile, invoice.file_id)
    if file is None:
        raise ValidationFailedError("票据文件记录不存在")
    return invoice, file


def _mark_invoice_running(db: Session, invoice: Invoice) -> None:
    """只允许 pending/running → running，不覆盖人工干预后的状态。"""
    db.execute(
        update(Invoice)
        .where(Invoice.id == invoice.id, Invoice.ocr_status.in_(_STATUS_OPEN))
        .values(ocr_status=OcrStatus.RUNNING.value)
    )
    db.commit()
    db.refresh(invoice)


def _download(storage: StorageService, file: InvoiceFile, settings: Settings) -> bytes:
    if file.is_cipher:
        # S4 接入信封后，这里改成"先解封再进内存"；现在绝不假装能读密文
        raise ValidationFailedError("加密影像解密尚未接入（S4）")
    return storage.get_bytes(file.storage_key, max_bytes=settings.max_upload_mb * 1024 * 1024)


def _classify(pages: list[RecognizedPage], templates: dict[str, Any]) -> Classification:
    fallback: Classification | None = None
    for page in pages:
        if not page.lines:
            continue
        classification = classify_ticket(page.lines, page.image_size, templates)
        if fallback is None:
            fallback = classification
        if classification.is_known:
            return classification
    return fallback or Classification("unknown", 0.0, "", [], {}, ["no_lines"])


def _resolve_rules(
    db: Session, deps: PipelineDeps, ticket_type: str
) -> tuple[list[ExtractorRule], str, int | None]:
    """抽取规格以 DB active 模板为准（S2-8 既定方向）；库缺失该票种才回退 YAML。"""
    registry = deps.registry or template_registry.registry
    snapshot = registry.active_templates(db).get(ticket_type)
    if snapshot is not None and snapshot.field_specs_json:
        return rules_from_specs(snapshot.field_specs_json), "db", snapshot.id
    return load_field_rules(), "yaml", None


def _attach_layout(
    pages: list[RecognizedPage], classification: Classification, templates: dict[str, Any]
) -> None:
    for page in pages:
        page.assignment = assign_regions(page.lines, page.image_size, classification, templates)
        page.blocks = {
            line_no: f"b{page.page_no}-{block.block_id}"
            for block in page.assignment.blocks
            for line_no in block.line_nos
        }


def _extract(
    pages: list[RecognizedPage], classification: Classification, rules: list[ExtractorRule]
) -> tuple[dict[str, MergedField], list[tuple[int, ExtractionResult]], list[str]]:
    results: list[tuple[int, ExtractionResult]] = []
    for page in pages:
        layout = (
            LayoutResult(classification=classification, regions=page.assignment)
            if page.assignment is not None
            else None
        )
        results.append(
            (
                page.page_no,
                extract_fields(page.lines, page.image_size, classification.ticket_type, layout, rules),
            )
        )
    merged, merge_warnings = merge_page_fields(results)
    return merged, results, merge_warnings


def _rule_ids(db: Session, template_id: int | None, names: Any) -> dict[str, int]:
    field_names = list(names)
    if template_id is None or not field_names:
        return {}
    rows = db.execute(
        select(FieldRule.field_name, FieldRule.id).where(
            FieldRule.template_id == template_id,
            FieldRule.field_name.in_(field_names),
            FieldRule.active.is_(True),
        )
    ).all()
    return {name: row_id for name, row_id in rows}


def _persist(
    db: Session,
    *,
    invoice: Invoice,
    job: OcrJob,
    pages: list[RecognizedPage],
    merged: dict[str, MergedField],
    failed_fields: set[str],
    classification: Classification,
    template_id: int | None,
    metrics: dict[str, Any],
) -> None:
    db.execute(delete(OcrLineRow).where(OcrLineRow.invoice_id == invoice.id))
    line_values = line_rows(pages, invoice_id=invoice.id, job_id=job.id)
    if line_values:
        db.execute(insert(OcrLineRow), line_values)

    # 人工改过/已确认的字段不覆盖（S3-3/S3-6 的前置保障）
    db.execute(
        delete(InvoiceField).where(
            InvoiceField.invoice_id == invoice.id,
            InvoiceField.source != FieldSource.MANUAL.value,
            InvoiceField.confirmed.is_(False),
        )
    )
    preserved = set(
        db.scalars(
            select(InvoiceField.field_name).where(
                InvoiceField.invoice_id == invoice.id,
                or_(
                    InvoiceField.source == FieldSource.MANUAL.value,
                    InvoiceField.confirmed.is_(True),
                ),
            )
        )
    )
    field_values = [
        row
        for row in field_rows(
            merged,
            invoice_id=invoice.id,
            rule_ids=_rule_ids(db, template_id, merged.keys()),
            failed_fields=failed_fields,
        )
        if row["field_name"] not in preserved
    ]
    if field_values:
        db.execute(insert(InvoiceField), field_values)

    quality_flag = pages[0].prepared.quality_flag if pages else None
    final = {
        "ocr_status": OcrStatus.SUCCEEDED.value,
        **({"quality_flag": quality_flag} if quality_flag in ("ok", "low") else {}),
    }
    values = invoice_values(merged, ticket_type=classification.ticket_type, template_id=template_id)
    try:
        with db.begin_nested():
            db.execute(
                update(Invoice)
                .where(Invoice.id == invoice.id, Invoice.ocr_status.in_(_STATUS_OPEN))
                .values(**values, **final)
            )
    except IntegrityError:
        # 同票号两张图：强重复的判定与返回是 S3-7 的职责；这里只保住本次识别结果
        metrics["duplicate_invoice_no"] = True
        metrics["warnings"].append("duplicate_invoice_no")
        db.execute(
            update(Invoice)
            .where(Invoice.id == invoice.id, Invoice.ocr_status.in_(_STATUS_OPEN))
            .values(invoice_type=classification.ticket_type, template_id=template_id, **final)
        )

    metrics["quality"] = {
        "flag": quality_flag,
        "needs_retake": pages[0].prepared.needs_retake if pages else False,
        "retake_hints": pages[0].prepared.retake_hints if pages else [],
        "reasons": [],
    }


def _flush_progress(
    db: Session, job: OcrJob, metrics: dict[str, Any], stage: str, *, commit: bool = True
) -> None:
    job.stage = stage
    # 每次赋一个新对象：同一个 dict 反复赋同一属性不会触发 flush（坑过一次）
    job.metrics = dict(metrics)
    if commit:
        db.commit()


def _record_failure(
    db: Session, job: OcrJob, stage: str, metrics: dict[str, Any], exc: BaseException
) -> None:
    """业务写入全部回滚，只把"哪个阶段失败"写进作业行（中途可见）。"""
    db.rollback()
    fresh = db.get(OcrJob, job.id)
    if fresh is None or fresh.status != OcrStatus.RUNNING.value:
        return
    _fail_stage(metrics, stage)
    metrics["warnings"] = _cap_warnings(metrics["warnings"])
    fresh.stage = stage if stage in PIPELINE_STAGES else OcrStage.PREPROCESS.value
    fresh.metrics = metrics
    db.commit()


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_pipeline(db: Session, job: OcrJob, *, deps: PipelineDeps | None = None) -> None:
    """跑完整条流水线；成功时不 commit（由 `job_service` 与作业终态一起提交）。"""
    deps = deps or PipelineDeps()
    settings = get_settings()
    options = OcrOptions.from_settings(settings)
    engine = deps.engine or get_engine(options)
    storage = deps.storage or get_storage()
    prepare = deps.prepare_pages or prepare_pages
    clock = deps.clock
    deadline = clock() + settings.ocr_pipeline_deadline_seconds
    metrics = _new_metrics(attempt=job.retry_count)
    stage = OcrStage.PREPROCESS.value

    try:
        if job.status != OcrStatus.RUNNING.value:
            raise AbortAttempt("作业不在 running 状态，跳过执行")

        invoice, file = _load_invoice(db, job)
        _mark_invoice_running(db, invoice)

        started = clock()
        source = _download(storage, file, settings)
        metrics["timings_ms"]["download"] = _ms(clock() - started)

        started = clock()
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
            path = Path(tmp) / "source.bin"
            path.write_bytes(source)
            prepared = prepare(
                path,
                max_pages=settings.ocr_pdf_max_pages,
                pdf_dpi=250,
            )
        # 图已读进内存，临时目录到这里就删干净（任务卡的坑①）
        metrics["timings_ms"]["intake"] = _ms(clock() - started)
        metrics["pages_total"] = len(prepared)
        _complete(metrics, OcrStage.PREPROCESS.value, metrics["timings_ms"]["intake"])
        _flush_progress(db, job, metrics, OcrStage.PREPROCESS.value)
        _check_deadline(deadline, clock)

        pages: list[RecognizedPage] = []
        ocr_started = clock()
        stage = OcrStage.RECOGNIZE.value
        for page in prepared:
            _check_deadline(deadline, clock)
            result = engine.recognize(page.image)
            pages.append(
                RecognizedPage(
                    page_no=page.page_no,
                    lines=result.lines,
                    image_size=result.image_size,
                    prepared=page,
                )
            )
        ocr_ms = _ms(clock() - ocr_started)
        metrics["timings_ms"]["ocr"] = ocr_ms
        metrics["pages_ocr"] = len(pages)
        metrics["counts"]["lines"] = sum(len(page.lines) for page in pages)
        metrics["engine"] = {
            "name": "paddleocr",
            "version": getattr(engine, "version", ""),
            "det_params": options.as_det_params(),
        }
        _complete(
            metrics,
            OcrStage.DETECT.value,
            None,
            note="DBNet 与 SVTR 在同一次 PaddleOCR 调用内完成，无法单独计时",
        )
        _complete(metrics, OcrStage.RECOGNIZE.value, ocr_ms)
        _flush_progress(db, job, metrics, OcrStage.RECOGNIZE.value)
        _check_deadline(deadline, clock)

        templates = deps.templates if deps.templates is not None else load_templates()
        classification = _classify(pages, templates)
        rules, rules_source, template_id = _resolve_rules(db, deps, classification.ticket_type)
        metrics["rules_source"] = rules_source
        metrics["template_id"] = template_id
        if rules_source == "yaml":
            metrics["warnings"].append(f"rules_yaml_fallback:{classification.ticket_type}")

        layout_started = clock()
        _attach_layout(pages, classification, templates)
        metrics["timings_ms"]["layout"] = _ms(clock() - layout_started)

        extract_started = clock()
        merged, results, merge_warnings = _extract(pages, classification, rules)
        metrics["warnings"].extend(merge_warnings)
        metrics["timings_ms"]["extract"] = _ms(clock() - extract_started)
        metrics["warnings"].extend(
            warning for _, result in results for warning in result.warnings
        )
        failed_fields = run_validators(
            {name: item.field for name, item in merged.items()}, classification.ticket_type
        ).failed_fields()
        metrics["counts"]["fields"] = len(merged)
        metrics["counts"]["missing_required"] = sorted(
            {name for _, result in results for name in result.missing_required}
        )
        metrics["counts"]["conflicts"] = sum(len(result.conflicts) for _, result in results)

        stage = OcrStage.EXTRACT.value
        persist_started = clock()
        _persist(
            db,
            invoice=invoice,
            job=job,
            pages=pages,
            merged=merged,
            failed_fields=failed_fields,
            classification=classification,
            template_id=template_id,
            metrics=metrics,
        )
        metrics["timings_ms"]["persist"] = _ms(clock() - persist_started)
        _complete(metrics, OcrStage.EXTRACT.value, metrics["timings_ms"]["extract"])
        metrics["timings_ms"]["total"] = sum(
            metrics["timings_ms"][key]
            for key in ("download", "intake", "ocr", "layout", "extract", "persist")
        )
        metrics["warnings"] = _cap_warnings(metrics["warnings"])

        # 不 commit：让 job_service 把业务写入与 succeeded 一次性提交（subperseded 守卫会回滚）
        job.stage = OcrStage.EXTRACT.value
        job.metrics = dict(metrics)
    except Exception as exc:  # noqa: BLE001 - 记录失败阶段后原样抛出，由 job_service 分类
        _record_failure(db, job, stage, metrics, exc)
        raise