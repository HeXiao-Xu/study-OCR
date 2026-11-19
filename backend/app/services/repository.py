"""行级数据隔离（API.md §0 权限矩阵；D-041）。

唯一入口：**所有**票据 / 文件 / 报销单的读查询都走 `filter_visible` 或 `get_visible_or_404`，
禁止在路由或 service 里各写一份 `where uploader_id = ...`——任务卡点名的翻车点就是"详情改了、列表忘了"。

可见性规则（角色以数据库为准）：
- submitter：只属于自己的行（`invoices/invoice_files.uploader_id`、`report_forms.applicant_id`）；
- reviewer：票据与文件全部可读；报销单**仅 submitted 之后**可见（草稿等不可见）；
- admin：业务表一律不可见（职责分离，D-017）——目标存在也返回 404；
- 未登记模型：`false()` 默认拒绝（新表 "忘了加隔离" 时安全，而不是全公开）。

对外语义：资源不存在与无权访问**都是 404/40400**（不泄露资源是否存在）；
内部区分 `NotFoundError` 与 `PermissionDeniedError`，给 S4-7 的 `action=forbidden` 审计留语义。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, false, select, true
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.enums import ReportStatus, UserRole
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.users import User

ScopeMode = Literal["all", "none", "condition"]

# reviewer 能看到"已提交之后"的报销单；草稿/待签/已签/已加密都还没交到财务
REVIEWER_REPORT_STATUSES: tuple[str, ...] = (
    ReportStatus.SUBMITTED.value,
    ReportStatus.APPROVED.value,
    ReportStatus.REJECTED.value,
    ReportStatus.ARCHIVED.value,
)


@dataclass(frozen=True)
class VisibilityPolicy:
    """一张表的行级可见性策略。"""

    owner_column: InstrumentedAttribute[Any]
    reviewer: ScopeMode = "none"
    admin: ScopeMode = "none"
    reviewer_condition: ColumnElement[bool] | None = None


# 只登记 S2-3 的三张核心表；其它模型在对应任务（S2-8/S4-7/S5）登记策略前默认拒绝
POLICIES: dict[type[Any], VisibilityPolicy] = {
    Invoice: VisibilityPolicy(Invoice.uploader_id, reviewer="all", admin="none"),
    InvoiceFile: VisibilityPolicy(InvoiceFile.uploader_id, reviewer="all", admin="none"),
    ReportForm: VisibilityPolicy(
        ReportForm.applicant_id,
        reviewer="condition",
        admin="none",
        reviewer_condition=ReportForm.status.in_(REVIEWER_REPORT_STATUSES),
    ),
}


def _role_condition(policy: VisibilityPolicy, user: User) -> ColumnElement[bool]:
    if user.role == UserRole.SUBMITTER.value:
        return policy.owner_column == user.id

    if user.role == UserRole.REVIEWER.value:
        mode, condition = policy.reviewer, policy.reviewer_condition
    elif user.role == UserRole.ADMIN.value:
        mode, condition = policy.admin, None
    else:
        return false()

    if mode == "all":
        return true()
    if mode == "condition" and condition is not None:
        return condition
    return false()


def scope_for(model: type[Any], user: User) -> ColumnElement[bool]:
    """返回该模型对该用户的可见性谓词；未登记模型默认不可见。"""
    policy = POLICIES.get(model)
    if policy is None:
        return false()
    return _role_condition(policy, user)


def filter_visible(stmt: Select[Any], model: type[Any], user: User) -> Select[Any]:
    """列表/导出/批量接口统一用它；保留调用方已有的 where。"""
    return stmt.where(scope_for(model, user))


def select_visible(model: type[Any], user: User) -> Select[Any]:
    return filter_visible(select(model), model, user)


def get_visible_or_404(db: Session, model: type[Any], entity_id: int, user: User) -> Any:
    """详情接口统一用它。

    正常路径只查一次；未命中时再判断"不存在"还是"存在但不可见"：
    后者抛 `PermissionDeniedError`（处理器同样转 404），保留内部语义供审计使用。
    """
    visible = db.scalar(
        filter_visible(select(model).where(model.id == entity_id), model, user)
    )
    if visible is not None:
        return visible

    if db.get(model, entity_id) is not None:
        raise PermissionDeniedError()
    raise NotFoundError()