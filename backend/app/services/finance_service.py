"""财务待审查询（S5-3）：列表 / 详情 / CSV 导出。

性能与安全约束（任务卡的坑）：

1. **列表不实时验签**：`verify_status` 只读 `signatures.verify_result`（提交时系统已验，S5-2/D-086），
   请求里不做任何密码运算；
2. **列表不带密文**：出参 schema 里根本没有 `ciphertext_*` / `wrapped_key_*` / `storage_path`；
3. **整页 2 条 SQL**（count + 分页）：`signatures` 的 active 部分唯一索引保证 join 不放大行数；
4. CSV 与列表共用同一筛选函数，保证"看到的 = 导出的"。
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationFailedError
from app.models.envelopes import Envelope
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.report_items import ReportItem
from app.models.signatures import Signature
from app.models.users import User
from app.schemas.common import PageResult
from app.schemas.finance import (
    FinanceAttachmentOut,
    FinanceDetailData,
    FinancePendingItem,
    FinancePendingQuery,
)
from app.services import report_service
from app.services.pagination import paginate
from app.services.repository import filter_visible
from app.services.report_service import VERIFY_FAILED_RESULTS
from app.utils.text import escape_like
from app.utils.time import BUSINESS_TZ, business_day_bounds

__all__ = ["CSV_LIMIT", "export_pending_csv", "get_detail", "list_pending"]

#: 单次导出上限：财务规模远小于此；超过就要求收窄筛选，避免一次拉爆进程
CSV_LIMIT = 10_000

#: "待审"= 已提交；已审核/驳回/归档不在此列表（S5-5/S5-6 各自的视图）
PENDING_STATUS = "submitted"

SORT_MAP = {
    "submitted_at": ReportForm.submitted_at,
    "total_amount": ReportForm.total_amount,
}

_CSV_HEADER = ["单号", "申请人", "金额", "票据数", "提交时间", "验签状态"]
_VERIFY_LABEL = {"unverified": "未验", "ok": "通过", "failed": "失败"}


def list_pending(
    db: Session, user: User, query: FinancePendingQuery
) -> PageResult[FinancePendingItem]:
    stmt = _pending_stmt(user, query)
    page = paginate(
        db,
        stmt,
        ReportForm,
        SORT_MAP,
        query,
        default="submitted_at",
        scalar=False,
    )
    return PageResult[FinancePendingItem](
        items=[_row_to_item(row) for row in page.items],
        total=page.total,
        page=page.page,
        page_size=page.page_size,
    )


def get_detail(db: Session, user: User, report_id: int) -> FinanceDetailData:
    # 可见性/角色：reviewer 只能看 submitted 之后；草稿与不存在都 404（不泄露存在性）
    detail = report_service.get_report_detail(db, user, report_id)
    applicant = db.get(User, detail.report.applicant_id)
    attachments = _attachments(db, report_id)
    return FinanceDetailData(
        report=detail.report,
        applicant_name=_applicant_name(
            applicant.real_name if applicant is not None else None,
            applicant.username if applicant is not None else None,
        ),
        report_version=db.scalar(
            select(ReportForm.version).where(ReportForm.id == report_id)
        )
        or 0,
        signature=detail.signature,
        envelope=detail.envelope,
        verify_status=detail.verify_status,
        attachments=attachments,
    )


def export_pending_csv(
    db: Session, user: User, query: FinancePendingQuery
) -> list[str]:
    """按当前筛选导出 CSV 分块（先取全量到内存再逐块 yield）。

    在**响应开始前**就完成取数与上限检查，避免流式过程中才报错（客户端只会看到半截文件）。
    """
    stmt = _pending_stmt(user, query)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    if int(total or 0) > CSV_LIMIT:
        raise ValidationFailedError(
            f"命中 {total} 单，超过单次导出上限 {CSV_LIMIT}，请收窄筛选条件"
        )

    rows = db.execute(
        stmt.order_by(ReportForm.submitted_at.asc(), ReportForm.id.asc())
    ).all()

    chunks: list[str] = []
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_CSV_HEADER)
    chunks.append("\ufeff" + buffer.getvalue())  # BOM：Excel 打开中文不乱码
    buffer.seek(0)
    buffer.truncate(0)

    for row in rows:
        form: ReportForm = row[0]
        item = _row_to_item(row)
        writer.writerow(
            [
                form.report_no,
                item.applicant_name,
                f"{form.total_amount:.2f}",
                form.item_count,
                _format_time(form.submitted_at),
                _VERIFY_LABEL[item.verify_status],
            ]
        )
        chunks.append(buffer.getvalue())
        buffer.seek(0)
        buffer.truncate(0)
    return chunks


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _pending_stmt(user: User, query: FinancePendingQuery):
    """待审列表的同一个 Select（列表/CSV/计数共用），避免"看到的与导出的不一致"。"""
    stmt = (
        select(ReportForm)
        .join(User, ReportForm.applicant_id == User.id)
        .add_columns(User.username, User.real_name, Signature.verify_result)
        .outerjoin(
            Signature,
            and_(
                Signature.form_id == ReportForm.id,
                Signature.status == "active",
            ),
        )
    )
    # 行级隔离仍走统一入口（reviewer 可见 submitted 之后），再收敛到"待审"
    stmt = filter_visible(stmt, ReportForm, user)
    stmt = stmt.where(ReportForm.status == PENDING_STATUS)

    if query.verify_status is not None:
        stmt = stmt.where(_verify_condition(query.verify_status))

    start, end = business_day_bounds(query.submitted_from, query.submitted_to)
    if start is not None:
        stmt = stmt.where(ReportForm.submitted_at >= start)
    if end is not None:
        stmt = stmt.where(ReportForm.submitted_at < end)

    if query.q:
        pattern = escape_like(query.q)
        stmt = stmt.where(
            or_(
                ReportForm.report_no.ilike(pattern, escape="\\"),
                User.username.ilike(pattern, escape="\\"),
                User.real_name.ilike(pattern, escape="\\"),
            )
        )
    return stmt


def _verify_condition(status: str):
    if status == "unverified":
        return or_(Signature.id.is_(None), Signature.verify_result.is_(None))
    if status == "ok":
        return Signature.verify_result == "ok"
    return Signature.verify_result.in_(tuple(sorted(VERIFY_FAILED_RESULTS)))


def _row_to_item(row) -> FinancePendingItem:
    form: ReportForm = row[0]
    return FinancePendingItem(
        report_id=form.id,
        report_no=form.report_no,
        applicant_id=form.applicant_id,
        applicant_name=_applicant_name(row.real_name, row.username),
        total_amount=form.total_amount,
        item_count=form.item_count,
        submitted_at=form.submitted_at,
        verify_status=report_service.verify_status_from_result(row.verify_result),
    )


def _applicant_name(real_name: str | None, username: str | None) -> str:
    return (real_name or "").strip() or (username or "").strip() or "—"


def _attachments(db: Session, report_id: int) -> list[FinanceAttachmentOut]:
    rows = db.execute(
        select(ReportItem, Invoice, InvoiceFile)
        .join(Invoice, ReportItem.invoice_id == Invoice.id)
        .join(InvoiceFile, Invoice.file_id == InvoiceFile.id)
        .where(ReportItem.form_id == report_id)
        .order_by(ReportItem.id.asc())
    ).all()
    if not rows:
        return []
    file_ids = [row[2].id for row in rows]
    # 一次查完全部文件级信封，避免逐行回查（文件级信封 form_id 为空、file_id 非空）
    envelopes = {
        envelope.file_id: envelope
        for envelope in db.scalars(
            select(Envelope).where(Envelope.file_id.in_(file_ids))
        ).all()
    }
    attachments: list[FinanceAttachmentOut] = []
    for _, invoice, file_row in rows:
        envelope = envelopes.get(file_row.id)
        attachments.append(
            FinanceAttachmentOut(
                file_id=file_row.id,
                invoice_id=invoice.id,
                filename=file_row.filename,
                mime=file_row.mime,
                size=file_row.size,
                sha256=file_row.sha256,
                page_count=file_row.page_count,
                envelope=(
                    report_service.envelope_out(db, envelope)
                    if envelope is not None
                    else None
                ),
            )
        )
    return attachments


def _format_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(BUSINESS_TZ).strftime("%Y-%m-%d %H:%M:%S")