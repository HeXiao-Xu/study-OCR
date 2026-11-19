"""ORM 模型汇总入口。

Alembic 的 `env.py` 只 import 本模块：`Base.metadata` 里必须已经注册全部 16 张表，
否则 autogenerate 会"看不见"没被导入的模型并生成删表操作。

表清单与顺序以 `docs/SCHEMA.md` §0.4 为准（顺序固定）。
"""
from __future__ import annotations

from app.models.audit_logs import AuditLog
from app.models.base import Base
from app.models.certificates import Certificate
from app.models.envelopes import Envelope
from app.models.field_rules import FieldRule
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.key_records import KeyRecord
from app.models.ocr_jobs import OcrJob
from app.models.ocr_lines import OcrLine
from app.models.report_forms import ReportForm
from app.models.report_items import ReportItem
from app.models.review_logs import ReviewLog
from app.models.signatures import Signature
from app.models.ticket_templates import TicketTemplate
from app.models.users import User

__all__ = [
    "AuditLog",
    "Base",
    "Certificate",
    "Envelope",
    "FieldRule",
    "Invoice",
    "InvoiceField",
    "InvoiceFile",
    "KeyRecord",
    "OcrJob",
    "OcrLine",
    "ReportForm",
    "ReportItem",
    "ReviewLog",
    "Signature",
    "TicketTemplate",
    "User",
]