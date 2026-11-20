"""`GET /invoices/{id}/ocr-status` 读模型（S3-1）。

`stages` 是**真数据**：来自 `ocr_jobs.metrics.stages`（流水线逐阶段写的状态与耗时），
`started_at`/`finished_at` 取作业行的真时间；没有 metrics 的历史作业退化成单条，不 500。

`progress` 的权重是契约的一部分（API.md §2），固定不按图动态加权——前端只需要一个
"能解释的进度条"：preprocess 10 / detect 30 / recognize 40 / extract 20。
detect 与 recognize 是引擎同一次调用，所以两者总是一起完成（进度从 10 直接跳到 80）。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import OcrStatus
from app.models.invoices import Invoice
from app.models.ocr_jobs import OcrJob
from app.models.users import User
from app.schemas.invoice import OcrStageOut, OcrStatusData
from app.services.repository import get_visible_or_404

__all__ = ["STAGE_WEIGHTS", "build_stages", "compute_progress", "get_ocr_status"]

STAGE_WEIGHTS: dict[str, int] = {"preprocess": 10, "detect": 30, "recognize": 40, "extract": 20}


def compute_progress(stages: list[dict], ocr_status: str) -> int:
    """已完成阶段权重累加；成功即 100（不必等 stages 齐全）。"""
    if ocr_status == OcrStatus.SUCCEEDED.value:
        return 100
    done = sum(
        STAGE_WEIGHTS.get(str(item.get("stage") or ""), 0)
        for item in stages
        if item.get("status") == OcrStatus.SUCCEEDED.value
    )
    return min(done, 100)


def build_stages(job: OcrJob | None, metrics: dict) -> list[OcrStageOut]:
    if job is None:
        return []

    raw = metrics.get("stages") or []
    if not raw:
        # S3-1 之前的作业没有 metrics：只能给"当前阶段 + 行级状态"，如实标注
        return [
            OcrStageOut(
                stage=job.stage,
                status=job.status,
                elapsed_ms=job.elapsed_ms,
                engine=job.engine,
                engine_version=job.engine_version,
                retry_count=job.retry_count,
                error=job.error if job.status == OcrStatus.FAILED.value else None,
            )
        ]

    stages: list[OcrStageOut] = []
    for item in raw:
        status = str(item.get("status") or "")
        stages.append(
            OcrStageOut(
                stage=str(item.get("stage") or ""),
                status=status,
                elapsed_ms=item.get("elapsed_ms"),
                note=item.get("note"),
                engine=job.engine,
                engine_version=job.engine_version,
                retry_count=job.retry_count,
                # 错误只挂在失败阶段上；其余阶段重复同一条错误会让前端误以为多处失败
                error=job.error if status == OcrStatus.FAILED.value else None,
            )
        )
    return stages


def get_ocr_status(db: Session, user: User, invoice_id: int) -> OcrStatusData:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    job = db.scalars(
        select(OcrJob)
        .where(OcrJob.invoice_id == invoice_id)
        .order_by(OcrJob.created_at.desc(), OcrJob.id.desc())
        .limit(1)
    ).first()
    metrics = dict(job.metrics or {}) if job is not None else {}
    stages = build_stages(job, metrics)

    return OcrStatusData(
        invoice_id=invoice.id,
        ocr_status=invoice.ocr_status,
        progress=compute_progress([stage.model_dump() for stage in stages], invoice.ocr_status),
        stages=stages,
        started_at=job.started_at if job is not None else None,
        finished_at=job.finished_at if job is not None else None,
    )