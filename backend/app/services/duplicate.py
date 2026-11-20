"""重复报销检测（S3-7）：强重复（duplicate_of/同票号/同影像）+ 弱重复（同金额/日期/销方）。

强重复**在确认时阻断**（`confirm` → 409）；弱重复只提示不阻断——硬阻断会误伤连号票。
影像维度是纵深防御：`uq_invoice_files_sha256` + 上传去重已让正常路径不可能重复。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.enums import InvoiceStatus
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.users import User
from app.services.repository import get_visible_or_404

__all__ = ["DuplicateMatch", "DuplicateReport", "check_duplicates", "get_duplicate_report"]

_VOID = InvoiceStatus.VOID.value
_WEAK_FIELDS = ("total_amount", "invoice_date", "seller_tax_no")


@dataclass(frozen=True)
class DuplicateMatch:
    invoice_id: int
    kind: str
    invoice_code: str | None
    invoice_no: str | None
    invoice_date: date | None
    total_amount: Decimal | None
    status: str
    created_at: datetime


@dataclass(frozen=True)
class DuplicateReport:
    invoice_id: int
    level: str
    matches: list[DuplicateMatch]


def _to_match(row: Invoice, kind: str) -> DuplicateMatch:
    return DuplicateMatch(
        invoice_id=row.id,
        kind=kind,
        invoice_code=row.invoice_code,
        invoice_no=row.invoice_no,
        invoice_date=row.invoice_date,
        total_amount=row.total_amount,
        status=row.status,
        created_at=row.created_at,
    )


def _code_no_matches(db: Session, invoice: Invoice) -> list[Invoice]:
    """同票号查询：正常路径被唯一索引挡住，仅历史/导入数据可能命中（纵深防御）。"""
    if not (invoice.invoice_code and invoice.invoice_no):
        return []
    return list(
        db.scalars(
            select(Invoice).where(
                Invoice.invoice_code == invoice.invoice_code,
                Invoice.invoice_no == invoice.invoice_no,
                Invoice.id != invoice.id,
                Invoice.status != _VOID,
            )
        )
    )


def _image_matches(db: Session, invoice: Invoice) -> list[Invoice]:
    """同影像：`uq_invoice_files_sha256` + 上传去重已保证唯一，这里兜底历史/导入数据。"""
    file_row = db.get(InvoiceFile, invoice.file_id)
    if file_row is None or not file_row.sha256:
        return []
    return list(
        db.scalars(
            select(Invoice)
            .join(InvoiceFile, InvoiceFile.id == Invoice.file_id)
            .where(
                InvoiceFile.sha256 == file_row.sha256,
                Invoice.id != invoice.id,
                Invoice.status != _VOID,
            )
        )
    )


def _weak_values(db: Session, invoice: Invoice) -> dict[str, str | None]:
    rows = db.scalars(
        select(InvoiceField).where(
            InvoiceField.invoice_id == invoice.id,
            InvoiceField.field_name.in_(_WEAK_FIELDS),
        )
    ).all()
    return {row.field_name: (row.normalized_value or row.field_value) for row in rows}


def _field_value_subquery(field_name: str, value: str):
    return select(InvoiceField.invoice_id).where(
        InvoiceField.field_name == field_name,
        InvoiceField.normalized_value == value,
    )


def _weak_matches(
    db: Session, invoice: Invoice, *, limit: int, exclude: set[int]
) -> list[Invoice]:
    values = _weak_values(db, invoice)
    total, day, tax = (values.get(name) for name in _WEAK_FIELDS)
    if not (total and day and tax):
        # 缺任一维不判弱重复：宁可漏报，也不误伤连号票
        return []
    stmt = select(Invoice).where(
        Invoice.id.in_(_field_value_subquery("total_amount", total)),
        Invoice.id.in_(_field_value_subquery("invoice_date", day)),
        Invoice.id.in_(_field_value_subquery("seller_tax_no", tax)),
        Invoice.id != invoice.id,
        Invoice.status != _VOID,
        Invoice.id.not_in(exclude),
    )
    if invoice.invoice_no:
        stmt = stmt.where(
            or_(Invoice.invoice_no.is_(None), Invoice.invoice_no != invoice.invoice_no)
        )
    return list(
        db.scalars(stmt.order_by(Invoice.created_at.desc(), Invoice.id.desc()).limit(limit))
    )


def check_duplicates(db: Session, invoice: Invoice, *, weak_limit: int = 20) -> DuplicateReport:
    strong: list[DuplicateMatch] = []
    seen = {invoice.id}

    if invoice.duplicate_of:
        row = db.get(Invoice, invoice.duplicate_of)
        if row is not None and row.status != _VOID:
            strong.append(_to_match(row, "invoice_no"))
            seen.add(row.id)

    for row in _code_no_matches(db, invoice):
        if row.id not in seen:
            strong.append(_to_match(row, "invoice_no"))
            seen.add(row.id)

    for row in _image_matches(db, invoice):
        if row.id not in seen:
            strong.append(_to_match(row, "image"))
            seen.add(row.id)

    weak = [
        _to_match(row, "weak")
        for row in _weak_matches(db, invoice, limit=weak_limit, exclude=seen)
    ]
    level = "strong" if strong else ("weak" if weak else "none")
    return DuplicateReport(invoice_id=invoice.id, level=level, matches=strong + weak)


def get_duplicate_report(db: Session, user: User, invoice_id: int) -> DuplicateReport:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    return check_duplicates(db, invoice)