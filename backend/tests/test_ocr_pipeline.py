"""OCR 流水线（S3-1）：纯函数单测 + 临时库集成（假 engine/storage/intake，不加载模型）。

单元部分不碰 DB/MinIO/模型；集成部分用 `temporary_database`，假实现只替换"引擎/存储/分页输入"，
落库、事务、唯一索引、提交语义都走真实实现——那才是容易出错的地方。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.ocr.types import ExtractedField, ExtractionResult
from app.services.ocr_pipeline import (
    MergedField,
    field_rows,
    invoice_values,
    line_rows,
    merge_page_fields,
)


# --------------------------------------------------------------------------- #
# 单元：合并 / 回填 / 行构造
# --------------------------------------------------------------------------- #
def _field(name: str, value: str, *, confidence: float = 0.9,
           candidates: list[str] | None = None) -> ExtractedField:
    return ExtractedField(
        name=name, value=value, raw_text=f"{name}:{value}", source_line_no=3,
        confidence=confidence, rule_id=f"{name}:text", bbox=(1, 2, 3, 4),
        candidates=candidates or [],
    )


def test_merge_page_fields_first_page_wins_and_records_conflict() -> None:
    page1 = ExtractionResult(fields={"total_amount": _field("total_amount", "100.00")},
                             ticket_type="vat_digital")
    page2 = ExtractionResult(
        fields={"total_amount": _field("total_amount", "200.00"),
                "seller_name": _field("seller_name", "某某公司")},
        ticket_type="vat_digital",
    )

    merged, warnings = merge_page_fields([(1, page1), (2, page2)])

    assert merged["total_amount"].field.value == "100.00"      # 首页优先，不覆盖
    assert merged["total_amount"].page_no == 1
    assert merged["seller_name"].page_no == 2                  # 后续页只补空
    assert "page_conflict:total_amount" in warnings


def test_merge_page_fields_without_pages_returns_empty() -> None:
    merged, warnings = merge_page_fields([])
    assert merged == {} and warnings == []


def test_invoice_values_converts_money_and_date_with_decimal() -> None:
    from datetime import date

    merged = {
        "total_amount": MergedField(_field("total_amount", "1234.56"), 1),
        "invoice_date": MergedField(_field("invoice_date", "2026-03-01"), 1),
        "invoice_no": MergedField(_field("invoice_no", "12345678"), 1),
    }

    values = invoice_values(merged, ticket_type="vat_digital", template_id=7)

    assert values["total_amount"] == Decimal("1234.56")
    assert values["invoice_date"] == date(2026, 3, 1)
    assert values["invoice_no"] == "12345678"
    assert values["invoice_type"] == "vat_digital"
    assert values["template_id"] == 7
    assert "ocr_status" not in values          # 状态单独写：唯一索引冲突回滚时不能把状态一起带走
    assert "amount" not in values              # 缺失字段不写 None


def test_invoice_values_ignores_unparseable_money() -> None:
    merged = {"total_amount": MergedField(_field("total_amount", "不是数字"), 1)}
    values = invoice_values(merged, ticket_type="unknown", template_id=None)
    assert "total_amount" not in values
    assert values["template_id"] is None


def test_line_rows_use_four_point_box_and_page_no() -> None:
    class _Line:
        line_no = 0
        text = "价税合计"
        score = 0.91234
        box = [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]]

    class _Page:
        page_no = 2
        lines = [_Line()]
        blocks = {0: "b2-1"}

    rows = line_rows([_Page()], invoice_id=5, job_id=9)

    assert rows[0]["invoice_id"] == 5 and rows[0]["job_id"] == 9 and rows[0]["page_no"] == 2
    assert rows[0]["score"] == Decimal("0.912")
    assert rows[0]["box_json"][0] == [1.0, 2.0]      # 四点原样保留（字段层才取外接框）
    assert rows[0]["block_id"] == "b2-1"


def test_field_rows_uses_resolved_confidence_for_need_review() -> None:
    from app.services.confidence import FieldConfidence

    merged = {
        "total_amount": MergedField(
            _field("total_amount", "100.00", confidence=0.70, candidates=["100.00", "200.00"]), 1),
        "buyer_name": MergedField(_field("buyer_name", "张三", confidence=0.97), 1),
    }

    rows = field_rows(
        merged, invoice_id=5, rule_ids={"total_amount": 11},
        confidences={
            "total_amount": FieldConfidence("total_amount", 0.70, "review"),
            "buyer_name": FieldConfidence("buyer_name", 0.97, "auto"),
        },
    )
    by_name = {row["field_name"]: row for row in rows}

    assert by_name["total_amount"]["conflict"] is True
    assert by_name["total_amount"]["conflict_value"] == "200.00"
    assert by_name["total_amount"]["need_review"] is True       # level=review
    assert by_name["total_amount"]["confidence"] == Decimal("0.700")
    assert by_name["total_amount"]["rule_id"] == 11
    assert by_name["buyer_name"]["need_review"] is False        # level=auto
    assert by_name["buyer_name"]["source"] == "ocr"
    assert by_name["buyer_name"]["source_page_no"] == 1
    assert by_name["buyer_name"]["bbox_json"] == [1, 2, 3, 4]


# --------------------------------------------------------------------------- #
# 集成：真实事务/落库（假 engine / storage / 分页输入）
# --------------------------------------------------------------------------- #
import secrets  # noqa: E402
import tempfile  # noqa: E402

import numpy as np  # noqa: E402
import time  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

from alembic import command  # noqa: E402
from sqlalchemy import create_engine, select, text as sa_text  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.exceptions import ServiceUnavailableError  # noqa: E402
from app.models.enums import OcrStatus  # noqa: E402
from app.models.invoice_fields import InvoiceField  # noqa: E402
from app.models.invoice_files import InvoiceFile  # noqa: E402
from app.models.invoices import Invoice  # noqa: E402
from app.models.ocr_jobs import OcrJob  # noqa: E402
from app.models.ocr_lines import OcrLine  # noqa: E402
from app.models.field_rules import FieldRule  # noqa: E402
from app.models.ticket_templates import TicketTemplate  # noqa: E402
from app.models.users import User  # noqa: E402
from app.ocr.detector import OcrContractError  # noqa: E402
from app.ocr.intake import PreparedPage  # noqa: E402
from app.ocr.types import OcrLine as OcrLineModel, OcrResult  # noqa: E402
from app.services import job_service  # noqa: E402
from app.services.ocr_pipeline import TEMP_PREFIX, PipelineDeps, run_pipeline  # noqa: E402
from tests.db_helpers import alembic_config, temporary_database  # noqa: E402


class _FakeLockStore:
    def __init__(self) -> None:
        self.locked: set[str] = set()

    def acquire(self, key: str, _ttl: int) -> bool:
        if key in self.locked:
            return False
        self.locked.add(key)
        return True

    def release(self, key: str) -> None:
        self.locked.discard(key)


def _line(text: str, line_no: int, *, score: float = 0.95, y: float = 20.0) -> OcrLineModel:
    return OcrLineModel(
        text=text, score=score, box=[[10.0, y], [600.0, y], [600.0, y + 30], [10.0, y + 30]],
        line_no=line_no, y_center=y + 15, x_left=10.0, height=30.0,
    )


def _vat_digital_lines() -> list[OcrLineModel]:
    return [
        _line("电子发票（普通发票） 全电发票 数电票", 0, y=10),
        _line("发票号码：12345678", 1, y=120),
        _line("开票日期：2026-03-01", 2, y=160),
        _line("价税合计（小写）￥1,234.56", 3, y=400),
    ]


class FakeEngine:
    version = "2.7.3"

    def __init__(self, pages: dict[int, list[OcrLineModel]], *, error: Exception | None = None,
                 on_call=None) -> None:
        self.pages = pages
        self.error = error
        self.on_call = on_call
        self.calls = 0

    def recognize(self, _image) -> OcrResult:
        self.calls += 1
        if self.on_call is not None:
            self.on_call(self.calls)
        if self.error is not None:
            raise self.error
        return OcrResult(lines=self.pages.get(self.calls - 1, []), image_size=(1000, 600),
                         elapsed_ms=10.0)


class FakeStorage:
    def __init__(self, payload: bytes = b"fake-image", *, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    def get_bytes(self, _key: str, *, max_bytes: int) -> bytes:
        if self.error is not None:
            raise self.error
        return self.payload


def _fake_pages(count: int = 1) -> list[PreparedPage]:
    import numpy as np

    return [
        PreparedPage(page_no=index + 1, image=np.zeros((600, 1000, 3), dtype=np.uint8),
                     kind="pdf", quality_flag=None)
        for index in range(count)
    ]


def _deps(engine, *, pages: int = 1, storage=None, clock=None, registry=None) -> PipelineDeps:
    return PipelineDeps(
        engine=engine,
        storage=storage or FakeStorage(),
        prepare_pages=lambda *args, **kwargs: _fake_pages(pages),
        templates=None,
        registry=registry,
        clock=clock or time.monotonic,
    )


def _counter_clock(first: list[float], fallback: float = 10_000.0):
    remaining = list(first)

    def clock() -> float:
        if remaining:
            return remaining.pop(0)
        return fallback

    return clock


@pytest.fixture(scope="module")
def pipeline_database() -> Iterator[URL]:
    with temporary_database("study_ocr_pipe_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(pipeline_database: URL):
    eng = create_engine(pipeline_database.render_as_string(hide_password=False), poolclass=NullPool)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def clean_db(engine):
    with engine.begin() as conn:
        conn.execute(sa_text("TRUNCATE users RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def owner(session_factory, clean_db) -> User:
    with session_factory() as db:
        user = User(username="alice", real_name="A", role="submitter", status="active")
        db.add(user)
        db.commit()
        return user


def _create_invoice(session_factory, uploader_id: int, *, is_cipher: bool = False,
                    overrides: dict | None = None) -> tuple[int, int]:
    with session_factory() as db:
        file = InvoiceFile(
            uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg", size=1024,
            sha256=secrets.token_hex(32), storage_key=f"invoices/{secrets.token_hex(8)}.bin",
            is_cipher=is_cipher,
        )
        db.add(file)
        db.flush()
        invoice = Invoice(uploader_id=uploader_id, file_id=file.id, **(overrides or {}))
        db.add(invoice)
        db.flush()
        job = job_service.create_job(db, invoice_id=invoice.id)
        db.commit()
        return invoice.id, job.id


def _job_error(session_factory, job_id: int) -> str | None:
    with session_factory() as db:
        return db.get(OcrJob, job_id).error


def _execute(session_factory, invoice_id: int, deps: PipelineDeps) -> tuple[str, int]:
    """按 job_service 的真实执行路径跑一次（含终态提交/失败分类）。"""
    with session_factory() as db:
        job_id = db.scalar(select(OcrJob.id).where(OcrJob.invoice_id == invoice_id))
    outcome = job_service.execute_job_attempt(
        job_id, attempt=1, handler=lambda db, job: run_pipeline(db, job, deps=deps),
        session_factory=session_factory, lock_store=_FakeLockStore(),
    )
    return outcome, job_id


@pytest.mark.integration
def test_run_pipeline_happy_path_writes_rows(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id)

    outcome, _ = _execute(session_factory, invoice_id, _deps(FakeEngine({0: _vat_digital_lines()})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        job = db.get(OcrJob, job_id)
        fields = {row.field_name: row for row in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice_id))}
        lines = db.scalars(select(OcrLine).where(OcrLine.invoice_id == invoice_id)).all()

        assert invoice.ocr_status == "succeeded"
        assert invoice.invoice_type == "vat_digital"
        assert invoice.invoice_no == "12345678"
        assert str(invoice.total_amount) == "1234.56"
        assert len(lines) == 4
        assert fields["total_amount"].confidence is not None
        assert fields["total_amount"].source_page_no == 1
        assert job.metrics["stages"][0]["stage"] == "preprocess"
        assert [item["stage"] for item in job.metrics["stages"]] == [
            "preprocess", "detect", "recognize", "extract"]
        assert job.metrics["stages"][1]["elapsed_ms"] is None      # detect 与 recognize 合并
        assert job.metrics["timings_ms"]["total"] > 0
        assert job.engine_version == "2.7.3"           # 论文复现要记引擎版本
        assert job.params_json["lang"] == "ch"


class _FakePreprocess:
    """只带 to_original 的假预处理结果：校正图 → 原图 = 0.5× 放大 + 平移 (10,-5)。"""

    to_original = np.array([[0.5, 0.0, 10.0], [0.0, 0.5, -5.0], [0.0, 0.0, 1.0]])


def _page_with_preprocess(page_no: int = 1):
    from app.ocr.intake import PreparedPage

    return PreparedPage(
        page_no=page_no,
        image=np.zeros((600, 1000, 3), dtype=np.uint8),
        kind="photo",
        quality_flag="ok",
        preprocess=_FakePreprocess(),
    )


@pytest.mark.integration
def test_run_pipeline_maps_field_bbox_to_original(session_factory, owner) -> None:
    """S3-3：`invoice_fields.bbox_json` 必须是**原图**坐标（ocr_lines 保持校正图坐标）。"""
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    deps = PipelineDeps(
        engine=FakeEngine({0: _vat_digital_lines()}),
        storage=FakeStorage(),
        prepare_pages=lambda *args, **kwargs: [_page_with_preprocess()],
        templates=None,
    )
    outcome, _ = _execute(session_factory, invoice_id, deps)

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        row = db.scalar(select(InvoiceField).where(
            InvoiceField.invoice_id == invoice_id,
            InvoiceField.field_name == "invoice_no",
        ))
        # 行框 [10,120,600,150]（校正图）→ 0.5× + 平移(10,-5) → [15,55,310,70]
        assert row.bbox_json == [15, 55, 310, 70]


@pytest.mark.integration
def test_run_pipeline_uses_db_template_rules_and_links_rule_id(session_factory, owner) -> None:
    from app.services.template_seed import build_seed_templates

    seed = next(item for item in build_seed_templates() if item.type_code == "vat_digital")
    with session_factory() as db:
        template = TicketTemplate(
            type_code=seed.type_code, type_name=seed.type_name,
            match_keywords=seed.match_keywords, field_specs_json=seed.field_specs_json,
            version=1, active=True,
        )
        db.add(template)
        db.flush()
        for rule in seed.rules:
            db.add(FieldRule(
                template_id=template.id, field_name=rule.field_name, pattern=rule.pattern,
                validator=rule.validator, error_msg=rule.error_msg, priority=rule.priority,
            ))
        db.commit()
        template_id = template.id

    from app.services.template_registry import registry

    registry.invalidate()          # 前面的用例把"无模板"缓存住了，这里必须让缓存失效

    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    outcome, _ = _execute(session_factory, invoice_id, _deps(FakeEngine({0: _vat_digital_lines()})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        job = db.get(OcrJob, job_id)
        invoice = db.get(Invoice, invoice_id)
        row = db.scalar(select(InvoiceField).where(InvoiceField.invoice_id == invoice_id,
                                                   InvoiceField.field_name == "total_amount"))
        assert job.metrics["rules_source"] == "db"
        assert job.metrics["template_id"] == template_id
        assert invoice.template_id == template_id
        assert row.rule_id is not None


@pytest.mark.integration
def test_run_pipeline_multipage_merges_pages(session_factory, owner) -> None:
    page1 = [_line("电子发票（普通发票）", 0, y=10), _line("价税合计（小写）￥100.00", 1, y=400)]
    page2 = [_line("价税合计（小写）￥200.00", 0, y=400),
             _line("销售方名称：某某公司", 1, y=450)]
    invoice_id, job_id = _create_invoice(session_factory, owner.id)

    outcome, _ = _execute(
        session_factory, invoice_id,
        _deps(FakeEngine({0: page1, 1: page2}), pages=2),
    )

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        job = db.get(OcrJob, job_id)
        pages = set(db.scalars(select(OcrLine.page_no).where(OcrLine.invoice_id == invoice_id)))
        assert pages == {1, 2}
        assert str(invoice.total_amount) == "100.00"          # 首页优先，不被第二页覆盖
        assert job.metrics["pages_ocr"] == 2
        assert "page_conflict:total_amount" in job.metrics["warnings"]


@pytest.mark.integration
def test_run_pipeline_empty_ocr_succeeds(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id)

    outcome, _ = _execute(session_factory, invoice_id, _deps(FakeEngine({0: []})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        assert db.get(Invoice, invoice_id).ocr_status == "succeeded"
        assert db.get(Invoice, invoice_id).invoice_type == "unknown"
        assert db.get(OcrJob, job_id).metrics["counts"]["lines"] == 0
        assert db.scalar(select(InvoiceField).where(InvoiceField.invoice_id == invoice_id)) is None


@pytest.mark.integration
def test_run_pipeline_cipher_file_fails_permanently(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id, is_cipher=True)

    outcome, _ = _execute(session_factory, invoice_id, _deps(FakeEngine({0: _vat_digital_lines()})))

    assert outcome == "failed"
    with session_factory() as db:
        job = db.get(OcrJob, job_id)
        assert "S4" in job.error
        assert job.retry_count == 1
        assert db.get(Invoice, invoice_id).ocr_status == "running"


@pytest.mark.integration
def test_run_pipeline_storage_unavailable_is_retryable(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    deps = _deps(FakeEngine({0: _vat_digital_lines()}),
                 storage=FakeStorage(error=ServiceUnavailableError("对象存储不可用")))

    with pytest.raises(job_service.RetryLater):
        _execute(session_factory, invoice_id, deps)

    with session_factory() as db:
        job = db.get(OcrJob, job_id)
        assert job.status == "pending"
        assert job.finished_at is None


@pytest.mark.integration
def test_run_pipeline_contract_error_not_retryable(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    deps = _deps(FakeEngine({}, error=OcrContractError("paddleocr 被升级到 3.x")))

    outcome, _ = _execute(session_factory, invoice_id, deps)

    assert outcome == "failed"
    with session_factory() as db:
        assert db.get(OcrJob, job_id).error.startswith("OcrContractError")


@pytest.mark.integration
def test_run_pipeline_deadline_exceeded_is_retryable(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    deps = _deps(FakeEngine({0: _vat_digital_lines()}),
                 clock=_counter_clock([0.0, 0.0, 0.0, 10_000.0]))

    with pytest.raises(job_service.RetryLater):
        _execute(session_factory, invoice_id, deps)

    with session_factory() as db:
        job = db.get(OcrJob, job_id)
        assert job.status == "pending"
        assert "TimeoutError" in job.error


@pytest.mark.integration
def test_run_pipeline_manual_field_not_overwritten(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    with session_factory() as db:
        db.add(InvoiceField(invoice_id=invoice_id, field_name="invoice_no",
                            field_value="人工改过", normalized_value="人工改过",
                            source="manual", confidence=None))
        db.commit()

    outcome, _ = _execute(session_factory, invoice_id, _deps(FakeEngine({0: _vat_digital_lines()})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        rows = {row.field_name: row for row in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice_id))}
        assert rows["invoice_no"].source == "manual"
        assert rows["invoice_no"].field_value == "人工改过"
        assert "total_amount" in rows


@pytest.mark.integration
def test_run_pipeline_duplicate_invoice_no_is_warning(session_factory, owner) -> None:
    # 先占住 (invoice_code, invoice_no)；本张票识别出来的号码与它相同
    existing_id, _ = _create_invoice(session_factory, owner.id, overrides={
        "invoice_code": "1234567890", "invoice_no": "12345678"})
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    lines = [
        _line("增值税电子普通发票 校验码 价税合计", 0, y=10),
        _line("发票代码：1234567890", 1, y=120),
        _line("发票号码：12345678", 2, y=150),
        _line("价税合计（小写）￥99.00", 3, y=400),
    ]

    outcome, _ = _execute(session_factory, invoice_id, _deps(FakeEngine({0: lines})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        job = db.get(OcrJob, job_id)
        assert job.metrics["duplicate_invoice_no"] is True
        assert invoice.invoice_no is None            # 冲突时整批 invoices 回填被放弃
        assert invoice.ocr_status == "succeeded"     # 但作业/状态不能跟着失败
        fields = db.scalars(select(InvoiceField).where(InvoiceField.invoice_id == invoice_id)).all()
        assert any(row.field_name == "invoice_no" for row in fields)   # 字段照样留给人工看


def _vat_digital_lines_full() -> list[OcrLineModel]:
    return [
        _line("电子发票（普通发票） 全电发票 数电票", 0, y=10),
        _line("发票号码：12345678", 1, y=120),
        _line("开票日期：2026-03-01", 2, y=160),
        _line("合计  ¥100.00  ¥13.00", 3, y=380),
        _line("价税合计（小写）￥113.00", 4, y=420),
    ]


@pytest.mark.integration
def test_run_pipeline_writes_review_levels_and_overall(session_factory, owner) -> None:
    """齐全样本：日日期/合计可免人工，8 位发票号对数电票不合规 → 整单待确认。"""
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    outcome, _ = _execute(session_factory, invoice_id,
                          _deps(FakeEngine({0: _vat_digital_lines_full()})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        job = db.get(OcrJob, job_id)
        rows = {row.field_name: row for row in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice_id))}
        assert invoice.extract_status == "need_confirm"   # 有 review/manual 字段
        assert invoice.overall_confidence is not None
        assert job.metrics["counts"]["review_levels"]["auto"] >= 1
        assert job.metrics["counts"]["needs_review"] >= 1
        assert rows["total_amount"].need_review is False  # 价税合计 100+13=113，免人工
        assert rows["invoice_date"].need_review is False


@pytest.mark.integration
def test_run_pipeline_conflict_forces_review(session_factory, owner) -> None:
    # 两条相同锚点、不同金额 → candidates>1：冲突必须人工，即使分数很高
    lines = [
        _line("增值税电子普通发票", 0, y=10),
        _line("价税合计（小写）￥100.00", 1, y=300),
        _line("价税合计（小写）￥200.00", 2, y=340),
    ]
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    outcome, _ = _execute(session_factory, invoice_id, _deps(FakeEngine({0: lines})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        row = db.scalar(select(InvoiceField).where(
            InvoiceField.invoice_id == invoice_id,
            InvoiceField.field_name == "total_amount",
        ))
        assert row.conflict is True
        assert row.need_review is True


@pytest.mark.integration
def test_run_pipeline_extract_status_matches_needs(session_factory, owner) -> None:
    """不写死"一定是 raw/need_confirm"，断言状态与字段实际需要一致（阈值调整后仍成立）。"""
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    outcome, _ = _execute(session_factory, invoice_id,
                          _deps(FakeEngine({0: _vat_digital_lines_full()})))

    assert outcome == "succeeded", _job_error(session_factory, job_id)
    with session_factory() as db:
        invoice = db.get(Invoice, invoice_id)
        job = db.get(OcrJob, job_id)
        rows = db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice_id)).all()
        needs = any(row.need_review for row in rows) or bool(
            job.metrics["counts"]["missing_required"])
        assert invoice.extract_status == ("need_confirm" if needs else "raw")
        assert float(invoice.overall_confidence) == pytest.approx(
            job.metrics["counts"]["overall_confidence"], abs=1e-3)


@pytest.mark.integration
def test_run_pipeline_temp_dir_cleaned(session_factory, owner) -> None:
    invoice_id, _ = _create_invoice(session_factory, owner.id)
    _execute(session_factory, invoice_id, _deps(FakeEngine({0: _vat_digital_lines()})))

    leftover = list(Path(tempfile.gettempdir()).glob(f"{TEMP_PREFIX}*"))
    assert leftover == []


@pytest.mark.integration
def test_run_pipeline_aborts_when_job_not_running(session_factory, owner) -> None:
    invoice_id, job_id = _create_invoice(session_factory, owner.id)
    with session_factory() as db:
        job = db.get(OcrJob, job_id)
        job.status = OcrStatus.FAILED.value
        db.commit()
        from app.services.job_service import AbortAttempt

        with pytest.raises(AbortAttempt):
            run_pipeline(db, job, deps=_deps(FakeEngine({0: _vat_digital_lines()})))


@pytest.mark.integration
def test_run_pipeline_marks_invoice_running_mid_flight(session_factory, owner) -> None:
    invoice_id, _ = _create_invoice(session_factory, owner.id)
    seen: list[str] = []

    def on_call(_call: int) -> None:
        with session_factory() as db:
            seen.append(db.get(Invoice, invoice_id).ocr_status)

    _execute(session_factory, invoice_id,
             _deps(FakeEngine({0: _vat_digital_lines()}, on_call=on_call)))

    assert seen == ["running"]        # 识别期间轮询应当看到 running