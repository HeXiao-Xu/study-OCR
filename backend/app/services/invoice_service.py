"""票据业务服务：建草稿、状态机、列表、详情、软删、字段修改。

状态机只有 `transition()` 一个入口（AGENTS §10.2 / 任务卡"别散落 if-else"）；
票据级状态按 D-011 只有 5 值，OCR 状态与报销单状态不混进来。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictError,
    FieldConfirmationRequiredError,
    NotFoundError,
    PermissionDeniedError,
    RoleForbiddenError,
    UnresolvedConflictError,
    ValidationFailedError,
)
from app.models.enums import ExtractStatus, FieldSource, InvoiceStatus, UserRole
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.ocr.validators import (
    ValidationIssue,
    parse_date,
    parse_money,
    validate_date,
    validate_invoice_code,
    validate_money_sum,
    validate_uscc,
)
from app.schemas.common import PageResult
from app.schemas.invoice import (
    ConflictOut,
    FieldChange,
    InvoiceDetailData,
    InvoiceFieldOut,
    InvoiceFieldsData,
    InvoiceFieldsUpdateData,
    InvoiceFieldsUpdateRequest,
    InvoiceListQuery,
    InvoiceOut,
    LatestJobOut,
)
from app.services.confidence import level_for
from app.services.pagination import paginate
from app.services.repository import filter_visible, get_visible_or_404
from app.utils.text import escape_like

logger = logging.getLogger("app.invoices")

INVOICE_FIELD_NAMES: tuple[str, ...] = (
    "invoice_code",
    "invoice_no",
    "invoice_date",
    "buyer_name",
    "buyer_tax_no",
    "seller_name",
    "seller_tax_no",
    "amount",
    "tax_amount",
    "total_amount",
    # 大写金额只用于"大写 vs 小写"交叉校验（S1-5）；它也是可编辑字段，
    # 否则详情页会显示一个永远无法消除的"待确认"（S3-4 由字段名白名单测试发现）
    "total_amount_upper",
    "check_code",
    "passenger_name",
)
AMOUNT_FIELDS = frozenset({"amount", "tax_amount", "total_amount"})
# passenger_name 只存 invoice_fields（invoices 表没有该列，SCHEMA 原文如此）
# invoices 表没有这两列：passenger_name（SCHEMA 原文）与大写金额（只在 invoice_fields）
_COLUMN_FIELDS = frozenset(INVOICE_FIELD_NAMES) - {"passenger_name", "total_amount_upper"}

SORT_MAP = {
    "created_at": Invoice.created_at,
    "invoice_date": Invoice.invoice_date,
    "total_amount": Invoice.total_amount,
}

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    InvoiceStatus.DRAFT.value: frozenset({InvoiceStatus.CONFIRMED.value, InvoiceStatus.VOID.value}),
    InvoiceStatus.CONFIRMED.value: frozenset(
        {InvoiceStatus.IN_REPORT.value, InvoiceStatus.VOID.value}
    ),
    InvoiceStatus.IN_REPORT.value: frozenset({InvoiceStatus.ARCHIVED.value}),
    InvoiceStatus.ARCHIVED.value: frozenset(),
    InvoiceStatus.VOID.value: frozenset(),
}


# --------------------------------------------------------------------------- #
# 建草稿（内部工厂；S2-6 上传成功后调用，不暴露 HTTP）
# --------------------------------------------------------------------------- #
def create_draft(
    db: Session,
    *,
    uploader_id: int,
    file_id: int,
    invoice_type: str = "unknown",
    **overrides: object,
) -> Invoice:
    file = db.get(InvoiceFile, file_id)
    if file is None or file.uploader_id != uploader_id:
        raise NotFoundError("票据文件不存在")

    invoice = Invoice(
        uploader_id=uploader_id,
        file_id=file_id,
        invoice_type=invoice_type,
        status=InvoiceStatus.DRAFT.value,
        ocr_status="pending",
        extract_status=ExtractStatus.RAW.value,
        **overrides,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


# --------------------------------------------------------------------------- #
# 状态机
# --------------------------------------------------------------------------- #
def transition(
    db: Session,
    invoice: Invoice,
    target: str,
    actor: User,
    *,
    expected_version: int | None = None,
) -> Invoice:
    if target not in {member.value for member in InvoiceStatus}:
        raise ValidationFailedError(f"非法状态：{target}")
    if expected_version is not None and invoice.version != expected_version:
        raise ConflictError(
            f"版本冲突：当前 version={invoice.version}，请求 version={expected_version}"
        )
    if invoice.status == target:
        return invoice

    allowed = ALLOWED_TRANSITIONS.get(invoice.status, frozenset())
    if target not in allowed:
        raise ConflictError(f"状态不允许：{invoice.status} → {target}")

    invoice.status = target
    invoice.version += 1
    db.commit()
    db.refresh(invoice)
    logger.info(
        "invoice 状态迁移",
        extra={
            "event": "invoice_transition",
            "invoice_id": invoice.id,
            "user_id": actor.id,
            "stage": target,
        },
    )
    return invoice


# --------------------------------------------------------------------------- #
# 列表 / 详情
# --------------------------------------------------------------------------- #
def list_invoices(db: Session, user: User, query: InvoiceListQuery) -> PageResult[InvoiceOut]:
    # 契约：admin 对票据列表是 404（仓库层给空列表不够，必须在入口拒绝）
    if user.role not in {UserRole.SUBMITTER.value, UserRole.REVIEWER.value}:
        raise NotFoundError()

    stmt = (
        select(Invoice)
        .join(InvoiceFile, Invoice.file_id == InvoiceFile.id)
        .add_columns(
            InvoiceFile.filename,
            InvoiceFile.mime,
            InvoiceFile.size,
            InvoiceFile.sha256,
            InvoiceFile.page_count,
        )
    )
    stmt = filter_visible(stmt, Invoice, user)
    stmt = _apply_filters(stmt, query)

    page = paginate(
        db, stmt, Invoice, SORT_MAP, query, default="created_at", scalar=False
    )
    return PageResult[InvoiceOut](
        items=[_invoice_out_from_row(row) for row in page.items],
        total=page.total,
        page=page.page,
        page_size=page.page_size,
    )


def get_invoice_detail(db: Session, user: User, invoice_id: int) -> InvoiceDetailData:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    file = db.get(InvoiceFile, invoice.file_id)
    if file is None:
        raise NotFoundError("票据文件不存在")

    fields = db.scalars(
        select(InvoiceField)
        .where(InvoiceField.invoice_id == invoice_id)
        .order_by(InvoiceField.id.asc())
    ).all()
    job = db.scalars(
        select(OcrJob)
        .where(OcrJob.invoice_id == invoice_id)
        .order_by(OcrJob.created_at.desc(), OcrJob.id.desc())
        .limit(1)
    ).first()

    return InvoiceDetailData(
        invoice=_invoice_out(invoice, file),
        fields=[_field_out(item) for item in fields],
        latest_job=_job_out(job) if job is not None else None,
    )


# --------------------------------------------------------------------------- #
# 软删
# --------------------------------------------------------------------------- #
def get_invoice_fields(db: Session, user: User, invoice_id: int) -> InvoiceFieldsData:
    """字段级识别结果（API.md §2）：原文/归一值/置信度/待确认/已存候选冲突。

    校验类冲突（如 money_sum_mismatch）不在这里重算——那是确认动作的职责（S3-6），
    两处各算一套规则必然漂移。
    """
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    rows = db.scalars(
        select(InvoiceField)
        .where(InvoiceField.invoice_id == invoice.id)
        .order_by(InvoiceField.field_name.asc())
    ).all()
    conflicts = [
        ConflictOut(
            field_name=row.field_name,
            reason="candidate_conflict",
            expected=row.field_value,
            actual=row.conflict_value,
        )
        for row in rows
        if row.conflict
    ]
    return InvoiceFieldsData(
        invoice_id=invoice.id,
        overall_confidence=invoice.overall_confidence,
        needs_confirm=[row.field_name for row in rows if row.need_review],
        conflicts=conflicts,
        items=[_field_out(row) for row in rows],
    )


def soft_delete(db: Session, user: User, invoice_id: int) -> None:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    if invoice.uploader_id != user.id:
        # 契约：DELETE 仅本人；reviewer 能读但不能删
        raise PermissionDeniedError()
    if invoice.status == InvoiceStatus.VOID.value:
        return  # 幂等：已删仍返回 204
    if invoice.status in (InvoiceStatus.IN_REPORT.value, InvoiceStatus.ARCHIVED.value):
        raise ConflictError(f"票据当前状态为 {invoice.status}，禁止删除")
    transition(db, invoice, InvoiceStatus.VOID.value, user)


# --------------------------------------------------------------------------- #
# 字段修改（契约 PATCH /invoices/{id}/fields）
# --------------------------------------------------------------------------- #
def update_fields(
    db: Session, user: User, invoice_id: int, payload: InvoiceFieldsUpdateRequest
) -> InvoiceFieldsUpdateData:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)

    if invoice.status != InvoiceStatus.DRAFT.value or invoice.extract_status not in (
        ExtractStatus.RAW.value,
        ExtractStatus.NEED_CONFIRM.value,
    ):
        raise ConflictError(
            f"当前状态（{invoice.status}/{invoice.extract_status}）不允许修改字段"
        )
    if invoice.version != payload.version:
        raise ConflictError(f"版本冲突：当前 version={invoice.version}")

    names = [item.field_name for item in payload.fields]
    unknown = [name for name in names if name not in INVOICE_FIELD_NAMES]
    if unknown:
        raise ValidationFailedError(f"字段名非法：{', '.join(unknown)}")

    if any(name in AMOUNT_FIELDS for name in names):
        if user.role == UserRole.SUBMITTER.value:
            raise RoleForbiddenError("提交人不能修改金额字段（D-017）")
        if not payload.confirm_amount:
            raise FieldConfirmationRequiredError()

    # 先全部规范化，任何一项非法就整体失败，不落半套修改
    normalized: dict[str, tuple[str, object]] = {
        item.field_name: _normalize_field(item.field_name, item.value)
        for item in payload.fields
    }

    existing = {
        row.field_name: row
        for row in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)
        ).all()
    }
    merged = _merged_values(invoice, existing)
    changes: list[FieldChange] = []

    try:
        for item in payload.fields:
            value, column_value = normalized[item.field_name]
            row = existing.get(item.field_name)
            before: str | None = None
            if row is not None:
                before = row.field_value if row.field_value is not None else row.normalized_value
            elif item.field_name in _COLUMN_FIELDS:
                column_before = getattr(invoice, item.field_name)
                before = None if column_before is None else str(column_before)

            if row is None:
                row = InvoiceField(invoice_id=invoice.id, field_name=item.field_name)
                db.add(row)

            row.field_value = item.value
            row.normalized_value = value
            row.source = FieldSource.MANUAL.value
            row.confidence = Decimal("1.000")
            row.confirmed = item.confirm
            row.need_review = not item.confirm
            row.updated_by = user.id
            row.updated_at = datetime.now(timezone.utc)
            row.conflict = False
            row.conflict_value = None

            merged[item.field_name] = value
            if item.field_name in _COLUMN_FIELDS:
                setattr(invoice, item.field_name, column_value)
            changes.append(FieldChange(field_name=item.field_name, before=before, after=value))

        conflicts = _collect_conflicts(merged)
        if conflicts:
            raise UnresolvedConflictError()

        # 没有冲突：清掉旧 OCR 留下的冲突标记（值已经对得上）
        for row in existing.values():
            row.conflict = False
            row.conflict_value = None

        invoice.extract_status = ExtractStatus.NEED_CONFIRM.value
        invoice.version += 1
        db.commit()
        db.refresh(invoice)
    except Exception:
        db.rollback()
        raise

    needs_confirm = list(
        db.scalars(
            select(InvoiceField.field_name)
            .where(
                InvoiceField.invoice_id == invoice.id,
                InvoiceField.need_review.is_(True),
            )
            .order_by(InvoiceField.field_name.asc())
        ).all()
    )
    logger.info(
        "invoice 字段修改",
        extra={
            "event": "invoice_fields_updated",
            "invoice_id": invoice.id,
            "user_id": user.id,
        },
    )
    return InvoiceFieldsUpdateData(
        invoice_id=invoice.id,
        version=invoice.version,
        status=invoice.status,
        updated=changes,
        needs_confirm=needs_confirm,
        conflicts=[],
    )


# --------------------------------------------------------------------------- #
# 内部助手
# --------------------------------------------------------------------------- #
def _apply_filters(stmt, query: InvoiceListQuery):
    if query.status is not None:
        stmt = stmt.where(Invoice.status == query.status)
    else:
        # 软删票据默认不返回；显式 status=void 时才查作废票
        stmt = stmt.where(Invoice.status != InvoiceStatus.VOID.value)

    if query.extract_status is not None:
        stmt = stmt.where(Invoice.extract_status == query.extract_status)
    if query.invoice_type is not None:
        stmt = stmt.where(Invoice.invoice_type == query.invoice_type)
    if query.q:
        pattern = escape_like(query.q)
        stmt = stmt.where(
            or_(
                Invoice.invoice_no.ilike(pattern, escape="\\"),
                Invoice.seller_name.ilike(pattern, escape="\\"),
            )
        )
    if query.date_from is not None:
        stmt = stmt.where(Invoice.invoice_date >= query.date_from)
    if query.date_to is not None:
        stmt = stmt.where(Invoice.invoice_date <= query.date_to)
    return stmt


def _invoice_columns(invoice: Invoice) -> dict[str, object]:
    return {column.name: getattr(invoice, column.name) for column in Invoice.__table__.columns}


def _invoice_out(invoice: Invoice, file: InvoiceFile) -> InvoiceOut:
    data = _invoice_columns(invoice)
    data.update(
        filename=file.filename,
        mime=file.mime,
        size=file.size,
        sha256=file.sha256,
        page_count=file.page_count,
    )
    return InvoiceOut.model_validate(data)


def _invoice_out_from_row(row) -> InvoiceOut:
    invoice: Invoice = row[0]
    data = _invoice_columns(invoice)
    data.update(
        filename=row.filename,
        mime=row.mime,
        size=row.size,
        sha256=row.sha256,
        page_count=row.page_count,
    )
    return InvoiceOut.model_validate(data)


def _field_out(field: InvoiceField) -> InvoiceFieldOut:
    # 派生档位（S3-2）：阈值只在 services/confidence.py，前端不要自己再抄一份
    review_level = (
        level_for(float(field.confidence)) if field.confidence is not None else None
    )
    return InvoiceFieldOut(
        id=field.id,
        invoice_id=field.invoice_id,
        field_name=field.field_name,
        field_value=field.field_value,
        normalized_value=field.normalized_value,
        raw_text=field.raw_text,
        confidence=field.confidence,
        bbox=field.bbox_json,
        source=field.source,
        source_page_no=field.source_page_no,
        source_line_no=field.source_line_no,
        rule_id=field.rule_id,
        need_review=field.need_review,
        conflict=field.conflict,
        conflict_value=field.conflict_value,
        confirmed=field.confirmed,
        updated_by=field.updated_by,
        updated_at=field.updated_at,
        review_level=review_level,
    )


def _job_out(job: OcrJob) -> LatestJobOut:
    return LatestJobOut(
        job_id=job.id,
        stage=job.stage,
        status=job.status,
        elapsed_ms=job.elapsed_ms,
        retry_count=job.retry_count,
        error=job.error,
    )


def _normalize_field(name: str, raw: str) -> tuple[str, object]:
    """返回 (归一化字符串, 写 invoices 列的值)。非法值抛 42200。"""
    text = raw.strip()

    if name in AMOUNT_FIELDS:
        amount = parse_money(text)
        if amount is None:
            raise ValidationFailedError(f"{name} 不是合法金额：{raw}")
        amount = amount.quantize(Decimal("0.01"))
        return f"{amount:.2f}", amount

    if name == "invoice_date":
        parsed = parse_date(text)
        if parsed is None:
            raise ValidationFailedError(f"开票日期无法解析：{raw}")
        return parsed.isoformat(), parsed

    if name == "invoice_code":
        digits = re.sub(r"\s", "", text)
        issue = validate_invoice_code(digits)
        if issue is not None:
            raise ValidationFailedError(issue.message)
        return digits, digits

    if name == "invoice_no":
        digits = re.sub(r"\s", "", text)
        if not re.fullmatch(r"\d{8}|\d{20}", digits):
            raise ValidationFailedError("发票号码应为 8 或 20 位数字")
        return digits, digits

    if name in ("buyer_tax_no", "seller_tax_no"):
        value = re.sub(r"[\s-]", "", text).upper()
        if value and len(value) not in (15, 18, 20):
            raise ValidationFailedError(f"{name} 应为 15 / 18 / 20 位")
        return value, value

    return text, text


def _merged_values(invoice: Invoice, existing: dict[str, InvoiceField]) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for name in _COLUMN_FIELDS:
        column_value = getattr(invoice, name)
        values[name] = None if column_value is None else str(column_value)
    for name, row in existing.items():
        values[name] = row.normalized_value if row.normalized_value is not None else row.field_value
    return values


def _collect_conflicts(values: dict[str, str | None]) -> list[ConflictOut]:
    """交叉校验：金额和、日期、代码/号码、18 位税号。税号 15/20 位只查长度（SCHEMA 允许）。"""
    issues: list[ValidationIssue] = []

    amount = parse_money(values.get("amount"))
    tax = parse_money(values.get("tax_amount"))
    total = parse_money(values.get("total_amount"))
    money_issue = validate_money_sum(amount, tax, total)
    if money_issue is not None:
        issues.append(money_issue)

    date_issue = validate_date(values.get("invoice_date"))
    if date_issue is not None:
        issues.append(date_issue)

    code_issue = validate_invoice_code(values.get("invoice_code"))
    if code_issue is not None:
        issues.append(code_issue)

    invoice_no = values.get("invoice_no")
    if invoice_no and not re.fullmatch(r"\d{8}|\d{20}", str(invoice_no)):
        issues.append(
            ValidationIssue("invoice_no", "invalid_invoice_no", f"发票号码格式非法：{invoice_no}")
        )

    for name in ("buyer_tax_no", "seller_tax_no"):
        value = values.get(name)
        if not value:
            continue
        digits = re.sub(r"[\s-]", "", str(value)).upper()
        if len(digits) == 18:
            issue = validate_uscc(digits)
            if issue is not None:
                issues.append(ValidationIssue(name, issue.code, issue.message))
        elif len(digits) not in (15, 18, 20):
            issues.append(
                ValidationIssue(name, "invalid_tax_no", f"{name} 应为 15 / 18 / 20 位")
            )

    conflicts: list[ConflictOut] = []
    for issue in issues:
        expected = actual = None
        if issue.code == "money_sum_mismatch":
            expected = f"{total:.2f}" if total is not None else None
            if amount is not None and tax is not None:
                actual = f"{amount + tax:.2f}"
            elif amount is not None:
                actual = f"{amount:.2f}"
        conflicts.append(
            ConflictOut(
                field_name=issue.field,
                reason=issue.code,
                expected=expected,
                actual=actual,
            )
        )
    return conflicts