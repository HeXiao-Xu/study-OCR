"""模板与规则管理（S2-8）：列表/详情/创建/升版本/试跑。

关键不变式（API.md §3 / SCHEMA §4.1）：
- 同 `type_code` 多版本共存，但**至多一条 `active=true`**（部分唯一索引兜底）；
- `PUT` 永远生成 `version+1` **新行**，旧行保留；规则先复制再按 `field_name` 覆盖；
- 正则来自后台，Pydantic 层已 `re.compile` + 长度校验；
- 写操作后立刻 `registry.invalidate()`，运行时最长 60s 内看到新版本（写后立即生效）。
"""
from __future__ import annotations

import io
import logging
import time
from typing import Any

from fastapi import UploadFile
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    OcrEngineError,
    PayloadTooLargeError,
    RoleForbiddenError,
    ValidationFailedError,
)
from app.models.enums import UserRole
from app.models.field_rules import FieldRule
from app.models.ticket_templates import TicketTemplate
from app.models.users import User
from app.schemas.common import PageResult
from app.schemas.template import (
    RuleIn,
    RuleOut,
    TemplateCreate,
    TemplateDetail,
    TemplateDetailData,
    TemplateListQuery,
    TemplateOut,
    TemplateTestData,
    TemplateUpdate,
    TestFieldOut,
)
from app.services.pagination import paginate
from app.services.template_registry import registry

logger = logging.getLogger("app.templates")

SORT_MAP = {
    "updated_at": TicketTemplate.updated_at,
    "version": TicketTemplate.version,
    "type_code": TicketTemplate.type_code,
}

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_STRATEGY_TO_DIRECTION = {
    "inline_right": "right",
    "below_center": "below",
    "table_column": "any",
    "label_neighbor": "any",
    "total_zone": "any",
}


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def list_templates(
    db: Session, user: User, query: TemplateListQuery
) -> PageResult[TemplateOut]:
    _require_read(user)
    stmt = select(TicketTemplate).where(TicketTemplate.active.is_(query.active))
    if query.type_code:
        stmt = stmt.where(TicketTemplate.type_code == query.type_code)

    page = paginate(db, stmt, TicketTemplate, SORT_MAP, query, default="updated_at")
    return PageResult[TemplateOut](
        items=[TemplateOut.model_validate(row) for row in page.items],
        total=page.total,
        page=page.page,
        page_size=page.page_size,
    )


def get_template(db: Session, user: User, template_id: int) -> TemplateDetailData:
    _require_read(user)
    template = db.get(TicketTemplate, template_id)
    if template is None:
        raise NotFoundError("模板不存在")
    rules = db.scalars(
        select(FieldRule)
        .where(FieldRule.template_id == template_id)
        .order_by(FieldRule.priority.asc(), FieldRule.id.asc())
    ).all()
    return TemplateDetailData(
        template=TemplateDetail.model_validate(template),
        rules=[RuleOut.model_validate(row) for row in rules],
    )


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #
def create_template(db: Session, user: User, payload: TemplateCreate) -> TemplateOut:
    _require_admin(user)
    if db.scalar(
        select(TicketTemplate).where(TicketTemplate.type_code == payload.type_code)
    ) is not None:
        raise ConflictError("type_code 已存在（同票种多版本请用 PUT 升版本）")

    template = TicketTemplate(
        type_code=payload.type_code,
        type_name=payload.type_name,
        match_keywords=payload.match_keywords,
        field_specs_json=payload.field_specs_json,
        version=1,
        active=payload.active,
    )
    try:
        db.add(template)
        db.flush()
        _insert_rules(db, template.id, payload.rules)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("type_code 已存在或 active 冲突") from exc

    db.refresh(template)
    registry.invalidate()
    logger.info(
        "模板创建",
        extra={"event": "template_created", "task_id": template.id},
    )
    return TemplateOut.model_validate(template)


def update_template(
    db: Session, user: User, template_id: int, payload: TemplateUpdate
) -> TemplateOut:
    _require_admin(user)
    current = db.get(TicketTemplate, template_id)
    if current is None:
        raise NotFoundError("模板不存在")
    if payload.type_code != current.type_code:
        raise ValidationFailedError("type_code 不允许修改")

    latest = db.scalar(
        select(TicketTemplate)
        .where(TicketTemplate.type_code == current.type_code)
        .order_by(TicketTemplate.version.desc())
        .limit(1)
    )
    if latest is None:
        raise NotFoundError("模板不存在")
    if payload.version != latest.version:
        raise ConflictError(
            "版本冲突",
            detail={"current_version": latest.version},
        )

    if payload.active:
        # 先停旧 active，再插新行：部分唯一索引 uq_ticket_templates_active_type 兜底
        db.execute(
            update(TicketTemplate)
            .where(
                TicketTemplate.type_code == current.type_code,
                TicketTemplate.active.is_(True),
            )
            .values(active=False)
        )
        db.flush()

    new_template = TicketTemplate(
        type_code=current.type_code,
        type_name=payload.type_name,
        match_keywords=payload.match_keywords,
        field_specs_json=payload.field_specs_json,
        version=latest.version + 1,
        active=payload.active,
    )
    try:
        db.add(new_template)
        db.flush()
        _copy_and_merge_rules(db, latest.id, new_template.id, payload.rules)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("版本冲突或 active 冲突") from exc

    db.refresh(new_template)
    registry.invalidate()
    logger.info(
        "模板升版本",
        extra={"event": "template_updated", "task_id": new_template.id},
    )
    return TemplateOut.model_validate(new_template)


# --------------------------------------------------------------------------- #
# 试跑（不落库、不写对象存储）
# --------------------------------------------------------------------------- #
def test_template(
    db: Session, user: User, template_id: int, upload: UploadFile, settings: Settings
) -> TemplateTestData:
    _require_admin(user)
    template = db.get(TicketTemplate, template_id)
    if template is None:
        raise NotFoundError("模板不存在")
    if not template.active:
        raise ValidationFailedError("模板未启用，不能试跑")

    data = _read_image(upload, settings.max_upload_mb)

    started = time.perf_counter()
    try:
        lines, image_size = _recognize_image(data)
    except OcrEngineError:
        raise
    except Exception as exc:  # noqa: BLE001 - 引擎异常统一 50010
        logger.exception("模板试跑 OCR 失败", extra={"event": "template_test_ocr_failed"})
        raise OcrEngineError("OCR 引擎异常") from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    from app.ocr.extractor import extract_fields  # 延迟导入：不在路由加载期拖入 cv2

    rules = _to_field_rules(template.field_specs_json)
    extraction = extract_fields(
        lines, image_size, ticket_type=template.type_code, rules=rules
    )
    text = " ".join(line.text for line in lines)
    matched = [
        keyword for keyword in (template.match_keywords or []) if keyword and keyword in text
    ]

    fields = [
        TestFieldOut(
            field_name=field.name,
            value=field.value,
            confidence=float(field.confidence),
            bbox=list(field.bbox) if field.bbox is not None else None,
            source="rule",
        )
        for field in extraction.fields.values()
    ]
    overall = (
        sum(float(field.confidence) for field in fields) / len(fields) if fields else 0.0
    )
    return TemplateTestData(
        template_id=template.id,
        type_code=template.type_code,
        matched_keywords=matched,
        overall_confidence=round(overall, 3),
        elapsed_ms=elapsed_ms,
        fields=fields,
    )


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _require_read(user: User) -> None:
    if user.role not in (UserRole.REVIEWER.value, UserRole.ADMIN.value):
        raise RoleForbiddenError("只有审核人/管理员可以查看模板")


def _require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN.value:
        raise RoleForbiddenError("只有管理员可以修改模板")


def _rule_data(rule: RuleIn) -> dict[str, Any]:
    return {
        "pattern": rule.pattern,
        "validator": rule.validator,
        "error_msg": rule.error_msg,
        "priority": rule.priority,
        "active": rule.active,
    }


def _insert_rules(db: Session, template_id: int, rules: list[RuleIn]) -> None:
    for rule in rules:
        db.add(FieldRule(template_id=template_id, field_name=rule.field_name, **_rule_data(rule)))


def _copy_and_merge_rules(
    db: Session, source_template_id: int, target_template_id: int, overrides: list[RuleIn]
) -> None:
    source_rows = db.scalars(
        select(FieldRule).where(FieldRule.template_id == source_template_id)
    ).all()
    merged: dict[str, dict[str, Any]] = {
        row.field_name: {
            "pattern": row.pattern,
            "validator": row.validator,
            "error_msg": row.error_msg,
            "priority": row.priority,
            "active": row.active,
        }
        for row in source_rows
    }
    for rule in overrides:
        merged[rule.field_name] = _rule_data(rule)

    for field_name, data in merged.items():
        db.add(
            FieldRule(
                template_id=target_template_id, field_name=field_name, **data
            )
        )


def _to_field_rules(specs: dict[str, Any]) -> list:
    """DB 的 field_specs_json → extractor.FieldRule（不改抽取算法，只换规则来源）。"""
    from app.ocr.extractor import FieldRule  # 延迟导入

    rules = []
    for name, spec in specs.items():
        if not isinstance(spec, dict):
            continue
        keywords = spec.get("keywords") or spec.get("anchors") or []
        direction = spec.get("direction") or _STRATEGY_TO_DIRECTION.get(
            str(spec.get("strategy") or ""), "right"
        )
        zone = spec.get("zone")
        rules.append(
            FieldRule(
                name=name,
                value_type=str(spec.get("value_type", "text")),
                anchors=[str(item) for item in keywords],
                direction=direction,
                regex=spec.get("regex") or spec.get("pattern"),
                max_distance=float(spec.get("max_distance") or 300.0),
                zone=(float(zone[0]), float(zone[1])) if zone else None,
                occurrence=str(spec.get("occurrence", "all")),
                tickets=spec.get("tickets"),
                required=bool(spec.get("required", False)),
                if_multiple=spec.get("if_multiple"),
                value_index=int(spec.get("value_index", 0)),
                exclude=spec.get("exclude"),
                whole_line_anchors=spec.get("whole_line_anchors"),
                rule_id=f"{name}:{spec.get('value_type', 'text')}",
            )
        )
    return rules


def _read_image(upload: UploadFile, max_upload_mb: int) -> bytes:
    data = upload.file.read()
    if not data:
        raise ValidationFailedError("空文件")
    if len(data) > max_upload_mb * 1024 * 1024:
        raise PayloadTooLargeError(f"文件超过 {max_upload_mb}MB 上限")

    head = data[: len(_PNG_MAGIC)]
    if not (head.startswith(_JPEG_MAGIC) or head.startswith(_PNG_MAGIC)):
        raise ValidationFailedError("模板试跑只支持 JPEG / PNG")

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:  # noqa: BLE001 - PIL 异常类型不稳定
        raise ValidationFailedError("文件不是有效图片") from exc
    return data


def _recognize_image(data: bytes):
    """生产路径：预处理 + PaddleOCR；测试 monkeypatch 这个函数，不加载模型。"""
    from app.ocr.detector import get_engine
    from app.ocr.preprocess import preprocess

    prepared = preprocess(data)
    result = get_engine().recognize(prepared.image)
    return result.lines, result.image_size