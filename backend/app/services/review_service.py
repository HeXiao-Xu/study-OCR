"""审核决策与驳回（S5-5）：approve 必须发生在验签之后，同一轮只能决策一次。

三条不变量（任务卡的坑）：

1. **verify_token 一次性**：approve 校验 Redis 里的 token → 决策 commit 后删除；
   同一 token 不可能批第二单（token 按 report 存），也不可能在同一单重复用（状态已变 → 409）。
2. **同一轮只决策一次**：SCHEMA §5.5 明确 `review_logs` 不加唯一约束（驳回重提会有多条审核记录），
   所以用"单据行 `FOR UPDATE` + `status='submitted'` 闸门"实现；并发第二个请求看到新状态 → 409。
3. **驳回不毁证据**：active 签名置 `void`（行保留）、`review_logs` 追加写；`comment` 正文不进审计。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.core.logging import get_trace_id
from app.models.enums import ReportStatus, SignatureStatus
from app.models.report_forms import ReportForm
from app.models.review_logs import ReviewLog
from app.models.signatures import Signature
from app.models.users import User
from app.schemas.finance import ReviewData, ReviewRequest
from app.services.audit import append_audit
from app.services.credential_store import CredentialStore, verify_key
from app.services.report_service import transition
from app.services.repository import get_visible_or_404

logger = logging.getLogger("app.finance.review")


def decide_report(
    db: Session,
    user: User,
    report_id: int,
    payload: ReviewRequest,
    *,
    store: CredentialStore,
) -> ReviewData:
    form = get_visible_or_404(db, ReportForm, report_id, user)

    try:
        # 行锁：并发决策串行化；第二个请求会看到 status 已变（而不是两条决策）
        form = db.execute(
            select(ReportForm)
            .where(ReportForm.id == form.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()

        if form.status != ReportStatus.SUBMITTED.value:
            raise ConflictError(
                f"单据当前状态为 {form.status}，不能重复决策",
                detail={"reason_code": "status_not_reviewable"},
            )
        if payload.version != form.version:
            raise ConflictError(
                f"版本冲突：当前 version={form.version}，请求 version={payload.version}",
                detail={"reason_code": "version_conflict"},
            )

        signature = _active_signature(db, form.id)
        if signature is None:
            raise ConflictError("单据缺少有效签名，不能决策")

        if payload.decision == "approve":
            stored = store.get(verify_key(form.id))
            if stored is None or stored != payload.verify_token:
                raise ConflictError(
                    "验签凭据无效或已过期（请先验签）",
                    detail={"reason_code": "verify_token_invalid"},
                )
            if signature.verify_result != "ok":
                raise ConflictError(
                    "验签未通过，不能通过审核",
                    detail={"reason_code": "verify_not_ok"},
                )

        comment = (payload.comment or "").strip() or None
        review = ReviewLog(
            form_id=form.id,
            reviewer_id=user.id,
            decision=payload.decision,
            comment=comment,
            # 以当前 active 签名的结论为准；verify 成功即证明信封已拆封并解密
            verify_result=signature.verify_result,
            decrypt_ok=signature.verify_result == "ok",
        )
        db.add(review)
        db.flush()

        if payload.decision == "approve":
            transition(db, form, ReportStatus.APPROVED.value, user, bump_version=False)
        else:
            transition(db, form, ReportStatus.REJECTED.value, user, bump_version=False)
            # 驳回后旧签名作废（记录保留），申请人重新签名会产生新版本
            signature.status = SignatureStatus.VOID.value
        form.version += 1

        append_audit(
            db,
            action="review",
            trace_id=get_trace_id(),
            actor_id=user.id,
            actor_role=user.role,
            target_type="form",
            target_id=form.id,
            detail={
                "report_no": form.report_no,
                "decision": payload.decision,
                "verify_result": signature.verify_result,
                "review_id": review.id,
                # 审核意见可能含敏感说明，只记长度
                "comment_len": len(comment) if comment else 0,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    # 一次性消费 verify_token：commit 之后删除（删除失败只告警；状态闸门仍能阻止重放）
    try:
        store.delete(verify_key(form.id))
    except Exception:  # noqa: BLE001
        logger.warning(
            "verify_token 删除失败",
            extra={"event": "verify_token_delete_failed", "report_id": form.id},
        )

    db.refresh(form)
    db.refresh(review)
    reviewed_at = review.created_at or datetime.now(timezone.utc)
    logger.info(
        "财务审核决策",
        extra={
            "event": "finance_review_decided",
            "report_id": form.id,
            "user_id": user.id,
            "decision": payload.decision,
        },
    )
    return ReviewData(
        report_id=form.id,
        report_no=form.report_no,
        status=form.status,
        decision=payload.decision,
        reviewed_at=reviewed_at,
    )


def _active_signature(db: Session, form_id: int) -> Signature | None:
    return db.scalars(
        select(Signature)
        .where(Signature.form_id == form_id, Signature.status == SignatureStatus.ACTIVE.value)
        .order_by(Signature.version.desc(), Signature.id.desc())
        .limit(1)
    ).first()