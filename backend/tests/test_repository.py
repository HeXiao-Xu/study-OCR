"""行级隔离谓词的单元测试（不连库，默认 `pytest` 就跑）。

这里守的是"策略表本身"：角色 → 谓词、未登记模型默认拒绝、过滤不覆盖调用方条件。
真实数据行为在 `tests/test_isolation.py`（integration）。
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.ticket_templates import TicketTemplate
from app.services.repository import (
    POLICIES,
    REVIEWER_REPORT_STATUSES,
    filter_visible,
    scope_for,
)


def _user(role: str, user_id: int = 7) -> SimpleNamespace:
    """`scope_for` 只读 `id` 与 `role`，不需要数据库对象。"""
    return SimpleNamespace(id=user_id, role=role)


def test_policies_cover_only_registered_tables() -> None:
    # 只登记 S2-3 的三张核心表；其它模型默认拒绝，等对应任务登记
    assert set(POLICIES) == {Invoice, InvoiceFile, ReportForm}


def test_submitter_scope_is_ownership() -> None:
    sql = str(scope_for(Invoice, _user("submitter")))
    assert "invoices.uploader_id" in sql


def test_submitter_report_scope_uses_applicant() -> None:
    sql = str(scope_for(ReportForm, _user("submitter")))
    assert "report_forms.applicant_id" in sql


def test_reviewer_scope_is_unrestricted_for_invoices() -> None:
    assert str(scope_for(Invoice, _user("reviewer"))) == "true"
    assert str(scope_for(InvoiceFile, _user("reviewer"))) == "true"


def test_admin_scope_is_denied_for_business_tables() -> None:
    assert str(scope_for(Invoice, _user("admin"))) == "false"
    assert str(scope_for(InvoiceFile, _user("admin"))) == "false"
    assert str(scope_for(ReportForm, _user("admin"))) == "false"


def test_reviewer_report_scope_matches_contract_statuses() -> None:
    # IN 默认渲染成 POSTCOMPILE 占位符，用 literal_binds 展开成实际取值
    sql = str(
        scope_for(ReportForm, _user("reviewer")).compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    assert "report_forms.status" in sql
    for status in REVIEWER_REPORT_STATUSES:
        assert f"'{status}'" in sql
    for hidden in ("draft", "pending_sign", "signed", "encrypted"):
        assert f"'{hidden}'" not in sql


def test_unregistered_model_denied_by_default() -> None:
    assert str(scope_for(TicketTemplate, _user("reviewer"))) == "false"
    assert str(scope_for(TicketTemplate, _user("submitter"))) == "false"


def test_unknown_role_denied() -> None:
    assert str(scope_for(Invoice, _user("superuser"))) == "false"


def test_filter_visible_preserves_caller_conditions() -> None:
    stmt = select(Invoice).where(Invoice.status == "draft")
    filtered = filter_visible(stmt, Invoice, _user("submitter"))
    sql = str(filtered)
    assert "invoices.status" in sql
    assert "invoices.uploader_id" in sql