"""报销单业务服务（S5-1）：单号、状态机、建单、加明细、详情、我的列表。

设计见 `docs/spec/s5-1-reports.md`，取舍见 D-084。四条纪律：

1. **一个用例一个事务**：`create_report` / `add_item` 自己 commit，失败整体 rollback；
   审计行与业务变更同事务（D-081），绝不出现"有单无审计"。
2. **合计只由服务端算**：`items.amount` 汇总，客户端传的合计一概不收（API.md §4）。
3. **一张票只能进一张有效单据**：票据行 `SELECT ... FOR UPDATE` 串行化，后到者看到
   `status='in_report'` 直接 40901；跨单查询只作兜底（SCHEMA 没有跨表唯一约束，D-084）。
4. **金额只走 `Decimal`**：float 累加会出现 0.01 漂移，让"合计自洽"变成随机事件。
"""
from __future__ import annotations

import logging
from base64 import b64decode
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    ConflictError,
    DuplicateInvoiceError,
    NotFoundError,
    RoleForbiddenError,
    UnresolvedConflictError,
    ValidationFailedError,
)
from app.core.logging import get_trace_id
from app.models.certificates import Certificate
from app.models.enums import (
    AuditAction,
    AuditTargetType,
    InvoiceStatus,
    ReportStatus,
    SignatureStatus,
    UserRole,
)
from app.models.envelopes import Envelope
from app.models.invoices import Invoice
from app.models.report_forms import ReportForm
from app.models.report_items import ReportItem
from app.models.signatures import Signature
from app.models.users import User
from app.schemas.common import PageResult
from app.schemas.report import (
    EnvelopeOut,
    ReportCreateRequest,
    ReportDetailData,
    ReportItemCreateRequest,
    ReportItemOut,
    ReportListQuery,
    ReportOut,
    SignatureOut,
)
from app.services.audit import append_audit
from app.services.pagination import paginate
from app.services.repository import filter_visible, get_visible_or_404

logger = logging.getLogger("app.reports")

#: 业务日/业务月换算只在这里做（AGENTS §4.3：一个地方换算）
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")

#: 可写明细的单据状态（API.md §4：仅 draft 或 rejected）
WRITABLE_STATUSES = frozenset({ReportStatus.DRAFT.value, ReportStatus.REJECTED.value})

#: 状态机（API.md §4 / SCHEMA §9.14）。`rejected → pending_sign` 是"驳回后重签"
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    ReportStatus.DRAFT.value: frozenset({ReportStatus.PENDING_SIGN.value}),
    ReportStatus.PENDING_SIGN.value: frozenset({ReportStatus.SIGNED.value}),
    ReportStatus.SIGNED.value: frozenset({ReportStatus.ENCRYPTED.value}),
    ReportStatus.ENCRYPTED.value: frozenset({ReportStatus.SUBMITTED.value}),
    ReportStatus.SUBMITTED.value: frozenset(
        {ReportStatus.APPROVED.value, ReportStatus.REJECTED.value}
    ),
    ReportStatus.APPROVED.value: frozenset({ReportStatus.ARCHIVED.value}),
    ReportStatus.REJECTED.value: frozenset({ReportStatus.PENDING_SIGN.value}),
    ReportStatus.ARCHIVED.value: frozenset(),
}

SORT_MAP = {
    "created_at": ReportForm.created_at,
    "submitted_at": ReportForm.submitted_at,
}


# --------------------------------------------------------------------------- #
# 状态机（唯一入口）
# --------------------------------------------------------------------------- #
def transition(
    db: Session,
    form: ReportForm,
    target: str,
    actor: User,
    *,
    expected_version: int | None = None,
    bump_version: bool = True,
) -> ReportForm:
    """把单据推进到 `target`；只 flush 不 commit，调用方决定事务边界（S5-2 要合事务）。

    `bump_version=False` 用于"一次请求内多次迁移、版本只 +1"的场景（S5-2 的 sign/submit），
    S5-1 的默认行为（每次迁移 +1）保持不变。
    """
    if target not in {member.value for member in ReportStatus}:
        raise ValidationFailedError(f"非法状态：{target}")
    if expected_version is not None and form.version != expected_version:
        raise ConflictError(
            f"版本冲突：当前 version={form.version}，请求 version={expected_version}"
        )
    if form.status == target:
        return form

    allowed = ALLOWED_TRANSITIONS.get(form.status, frozenset())
    if target not in allowed:
        raise ConflictError(f"状态不允许：{form.status} → {target}")

    form.status = target
    if bump_version:
        form.version += 1
    db.flush()
    logger.info(
        "report 状态迁移",
        extra={
            "event": "report_transition",
            "report_id": form.id,
            "user_id": actor.id,
            "stage": target,
        },
    )
    return form


# --------------------------------------------------------------------------- #
# 建单
# --------------------------------------------------------------------------- #
def create_report(db: Session, user: User, payload: ReportCreateRequest) -> ReportOut:
    _require_creator(user)
    form = ReportForm(
        report_no=_next_report_no(db),
        applicant_id=user.id,
        title=payload.title,
        reason=payload.reason,
        total_amount=Decimal("0.00"),
        item_count=0,
        status=ReportStatus.DRAFT.value,
    )
    try:
        db.add(form)
        db.flush()
        # 审计与建单同事务：建单失败不会留下审计行，链也不会被回滚打断（D-081）
        append_audit(
            db,
            action=AuditAction.REPORT_CREATE.value,
            trace_id=get_trace_id(),
            actor_id=user.id,
            actor_role=user.role,
            target_type=AuditTargetType.FORM.value,
            target_id=form.id,
            # 只记单号与状态：标题/事由/金额都不是审计需要的身份信息
            detail={"report_no": form.report_no, "status": form.status},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(form)
    logger.info(
        "报销单创建",
        extra={"event": "report_created", "report_id": form.id, "user_id": user.id},
    )
    return _report_out(form, items=[], department=user.dept)


# --------------------------------------------------------------------------- #
# 加明细
# --------------------------------------------------------------------------- #
def add_item(
    db: Session, user: User, report_id: int, payload: ReportItemCreateRequest
) -> ReportOut:
    form = get_visible_or_404(db, ReportForm, report_id, user)
    _require_owner_writer(user)

    try:
        # 先锁单据行再锁票据行（固定顺序，避免两把锁交叉造成死锁）：
        # 同一单据的并发加明细在这里排队，`_recalculate` 才不会读到别人未提交的中间态
        # 而算出"丢一条"的合计。`populate_existing` 让状态校验看到锁内最新值。
        form = db.execute(
            select(ReportForm)
            .where(ReportForm.id == form.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        if form.status not in WRITABLE_STATUSES:
            raise ConflictError(f"单据当前状态为 {form.status}，不允许添加明细")

        # 锁票据行：同一张票的并发添加在这行上排队，后到者看到最新状态（D-084）
        invoice = db.execute(
            select(Invoice).where(Invoice.id == payload.invoice_id).with_for_update()
        ).scalar_one_or_none()
        if invoice is None or invoice.uploader_id != user.id:
            # 非本人与不存在同响应，不泄露票据是否存在
            raise NotFoundError("票据不存在")

        # 同单重复先判：并发时由 UNIQUE(form_id, invoice_id) 兜底
        if db.scalar(
            select(ReportItem.id)
            .where(ReportItem.form_id == form.id, ReportItem.invoice_id == invoice.id)
            .limit(1)
        ) is not None:
            raise DuplicateInvoiceError(
                "该票据已在当前单据中", detail={"invoice_id": invoice.id}
            )

        if invoice.status == InvoiceStatus.IN_REPORT.value:
            raise DuplicateInvoiceError(
                "该票据已被其他有效单据引用", detail={"invoice_id": invoice.id}
            )
        if invoice.status != InvoiceStatus.CONFIRMED.value:
            raise ConflictError(
                f"票据当前状态为 {invoice.status}，只有已确认票据可报销"
            )

        total = invoice.total_amount or Decimal("0.00")
        if payload.amount > total:
            raise UnresolvedConflictError(
                "报销金额超过票面合计",
                detail={
                    "invoice_id": invoice.id,
                    "expected": f"{total:.2f}",
                    "actual": f"{payload.amount:.2f}",
                },
            )

        # 跨单兜底：正常路径上"别的有效单据引用过"会让票据已是 in_report，上面就拦了；
        # 这一条防的是绕过服务层写进来的脏数据（SCHEMA 没有跨表唯一约束）
        if db.scalar(
            select(ReportItem.id)
            .join(ReportForm, ReportItem.form_id == ReportForm.id)
            .where(
                ReportItem.invoice_id == invoice.id,
                ReportItem.form_id != form.id,
                ReportForm.status != ReportStatus.REJECTED.value,
            )
            .limit(1)
        ) is not None:
            raise DuplicateInvoiceError(
                "该票据已被其他有效单据引用", detail={"invoice_id": invoice.id}
            )

        item = ReportItem(
            form_id=form.id,
            invoice_id=invoice.id,
            amount=payload.amount,
            remark=payload.remark,
        )
        db.add(item)
        # 跨表不变式（SCHEMA §5.2）：明细加入成功即置 in_report，同事务
        invoice.status = InvoiceStatus.IN_REPORT.value
        invoice.version += 1
        db.flush()

        _recalculate(db, form)
        form.version += 1  # 单据内容变了，乐观锁计数要跟着走
        db.commit()
    except IntegrityError as exc:
        # 唯一约束 UNIQUE(form_id, invoice_id) 被并发撞到：同样是"重复添加"
        db.rollback()
        raise DuplicateInvoiceError(
            "该票据已在当前单据中", detail={"invoice_id": payload.invoice_id}
        ) from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(form)
    items = _list_items(db, form.id)
    logger.info(
        "报销明细添加",
        extra={
            "event": "report_item_added",
            "report_id": form.id,
            "invoice_id": payload.invoice_id,
            "user_id": user.id,
        },
    )
    return _report_out(form, items=items, department=_applicant_dept(db, form.applicant_id))


# --------------------------------------------------------------------------- #
# 详情 / 我的列表
# --------------------------------------------------------------------------- #
def get_report_detail(db: Session, user: User, report_id: int) -> ReportDetailData:
    form = get_visible_or_404(db, ReportForm, report_id, user)
    items = _list_items(db, form.id)
    signature = db.scalars(
        select(Signature)
        .where(
            Signature.form_id == form.id,
            Signature.status == SignatureStatus.ACTIVE.value,
        )
        .order_by(Signature.version.desc(), Signature.id.desc())
        .limit(1)
    ).first()
    envelope = db.scalars(
        select(Envelope).where(Envelope.form_id == form.id).limit(1)
    ).first()
    return ReportDetailData(
        report=_report_out(
            form, items=items, department=_applicant_dept(db, form.applicant_id)
        ),
        signature=SignatureOut.model_validate(signature) if signature is not None else None,
        envelope=_envelope_out(db, envelope) if envelope is not None else None,
        verify_status=_verify_status(signature),
    )


def list_my_reports(
    db: Session, user: User, query: ReportListQuery
) -> PageResult[ReportOut]:
    # 契约：reviewer/admin 对本接口是 403（API.md §4，角色级拒绝，不是"看不见"）
    if user.role != UserRole.SUBMITTER.value:
        raise RoleForbiddenError("只有提交人可以查看我的报销单")

    stmt = (
        select(ReportForm)
        .join(User, ReportForm.applicant_id == User.id)
        .add_columns(User.dept)
    )
    # 行级隔离仍走统一入口：submitter 只看得见本人
    stmt = filter_visible(stmt, ReportForm, user)
    stmt = _apply_filters(stmt, query)

    page = paginate(
        db,
        stmt,
        ReportForm,
        SORT_MAP,
        query,
        default="created_at",
        scalar=False,
        # submitted_at 可空：DESC 默认 NULLS FIRST 会让草稿盖住已提交单据
        nulls_last=True,
    )
    return PageResult[ReportOut](
        items=[_report_out_from_row(row) for row in page.items],
        total=page.total,
        page=page.page,
        page_size=page.page_size,
    )


# --------------------------------------------------------------------------- #
# 内部助手
# --------------------------------------------------------------------------- #
def require_submitter(user: User, *, action: str = "执行该操作") -> None:
    """报销单写操作的统一角色闸门：admin 404（职责分离）、reviewer 403（角色级）。"""
    if user.role == UserRole.ADMIN.value:
        raise NotFoundError()
    if user.role != UserRole.SUBMITTER.value:
        raise RoleForbiddenError(f"只有提交人可以{action}")


def _require_creator(user: User) -> None:
    """建单：admin 404（职责分离）、reviewer 403（角色级）。"""
    require_submitter(user, action="创建报销单")


def _require_owner_writer(user: User) -> None:
    require_submitter(user, action="维护报销明细")


def _next_report_no(db: Session) -> str:
    """`RPT-{YYYYMM}-{序号}`：序列非事务性，回滚不退号但绝不重号。

    用字面 SQL 而不是 `func.nextval(:param)`：PG 的 `nextval` 形参是 `regclass`，
    绑定参数是 text，不保证能隐式转换；序列名是代码常量，没有注入面。
    """
    seq = db.execute(text("SELECT nextval('report_no_seq')")).scalar_one()
    month = datetime.now(BUSINESS_TZ).strftime("%Y%m")
    return f"RPT-{month}-{int(seq):04d}"


def _recalculate(db: Session, form: ReportForm) -> None:
    """合计/条数只从 `report_items` 汇总（不信客户端）；空单回 0.00。"""
    total, count = db.execute(
        select(
            func.coalesce(func.sum(ReportItem.amount), 0),
            func.count(ReportItem.id),
        ).where(ReportItem.form_id == form.id)
    ).one()
    form.total_amount = Decimal(str(total)).quantize(Decimal("0.01"))
    form.item_count = int(count)


def _list_items(db: Session, form_id: int) -> list[ReportItem]:
    return list(
        db.scalars(
            select(ReportItem).where(ReportItem.form_id == form_id).order_by(ReportItem.id.asc())
        ).all()
    )


def _applicant_dept(db: Session, applicant_id: int) -> str | None:
    return db.scalar(select(User.dept).where(User.id == applicant_id))


def _verify_status(signature: Signature | None) -> str:
    """没有签名 → unverified；有签名但还没验 → unverified；验过 → ok / failed。"""
    if signature is None or signature.verify_result is None:
        return "unverified"
    return "ok" if signature.verify_result == "ok" else "failed"


def _envelope_out(db: Session, envelope: Envelope) -> EnvelopeOut:
    fingerprint = db.scalar(
        select(Certificate.fingerprint_sha256).where(
            Certificate.id == envelope.recipient_cert_id
        )
    )
    try:
        wrapped_len = len(b64decode(envelope.wrapped_key_b64, validate=True))
    except (ValueError, TypeError):  # 脏数据不影响详情可用性
        wrapped_len = None
    return EnvelopeOut(
        id=envelope.id,
        form_id=envelope.form_id,
        file_id=envelope.file_id,
        recipient_cert_id=envelope.recipient_cert_id,
        key_algo=envelope.key_algo,
        cipher_algo=envelope.cipher_algo,
        iv_b64=envelope.iv_b64,
        mac_b64=envelope.mac_b64,
        payload_sha256=envelope.payload_sha256,
        recipient_cert_fp=fingerprint,
        wrapped_key_len=wrapped_len,
        created_at=envelope.created_at,
    )


def _date_bounds(query: ReportListQuery) -> tuple[datetime | None, datetime | None]:
    """业务日 [date_from, date_to] → UTC 半开区间；只在这里按 Asia/Shanghai 换算。"""
    start = end = None
    if query.date_from is not None:
        start = datetime.combine(query.date_from, time.min, tzinfo=BUSINESS_TZ).astimezone(
            timezone.utc
        )
    if query.date_to is not None:
        end = datetime.combine(
            query.date_to + timedelta(days=1), time.min, tzinfo=BUSINESS_TZ
        ).astimezone(timezone.utc)
    if start is not None and end is not None and start >= end:
        raise ValidationFailedError("date_from 不能晚于 date_to")
    return start, end


def _apply_filters(stmt, query: ReportListQuery):
    if query.status is not None:
        stmt = stmt.where(ReportForm.status == query.status)
    start, end = _date_bounds(query)
    if start is not None:
        stmt = stmt.where(ReportForm.created_at >= start)
    if end is not None:
        stmt = stmt.where(ReportForm.created_at < end)
    return stmt


def _report_out(
    form: ReportForm, *, items: list[ReportItem], department: str | None
) -> ReportOut:
    return ReportOut(
        id=form.id,
        report_no=form.report_no,
        applicant_id=form.applicant_id,
        department=department,
        title=form.title,
        reason=form.reason,
        status=form.status,
        total_amount=form.total_amount,
        item_count=form.item_count,
        digest_algo=form.digest_algo,
        digest_hex=form.digest_hex,
        submitted_at=form.submitted_at,
        created_at=form.created_at,
        updated_at=form.updated_at,
        items=[ReportItemOut.model_validate(item) for item in items],
    )


def _report_out_from_row(row) -> ReportOut:
    form: ReportForm = row[0]
    # 列表不给明细（API.md §4 备注：避免 N+1），items 保持空
    return _report_out(form, items=[], department=row.dept)