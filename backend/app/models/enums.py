"""数据库枚举常量（SCHEMA §9 的唯一代码侧来源）。

纪律（AGENTS §4.2）：
- 枚举一律 `TEXT + CHECK`，不用 PostgreSQL ENUM 类型（加值不锁表）；
- 取值只在本文件定义，**CHECK 文本由同一份常量拼接**，迁移里禁止手抄字符串；
- 迁移文件是生成当时的快照；新增取值 = 改本文件 + 新迁移（先改 `docs/SCHEMA.md`）。
"""
from __future__ import annotations

from enum import StrEnum

from sqlalchemy import CheckConstraint

__all__ = [
    "AuditAction",
    "AuditActorRole",
    "AuditTargetType",
    "CertOwnerType",
    "CipherAlgo",
    "DigestAlgo",
    "EnvelopeKeyAlgo",
    "ExtractStatus",
    "FieldSource",
    "FieldValidator",
    "InvoiceStatus",
    "KeyAlgo",
    "KeyType",
    "OcrStage",
    "OcrStatus",
    "QualityFlag",
    "ReportStatus",
    "ReviewDecision",
    "SignatureAlgo",
    "SignatureStatus",
    "TicketType",
    "UserRole",
    "UserStatus",
    "VerifyResult",
    "enum_check",
    "enum_values",
]


class UserRole(StrEnum):
    SUBMITTER = "submitter"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class UserStatus(StrEnum):
    """locked 用于连续登录失败；用户不物理删除（签名与审计要追溯）。"""

    ACTIVE = "active"
    DISABLED = "disabled"
    LOCKED = "locked"


class CertOwnerType(StrEnum):
    USER = "user"
    SERVER = "server"


class KeyAlgo(StrEnum):
    SM2 = "SM2"
    RSA = "RSA"


class KeyType(StrEnum):
    SIGN = "sign"
    ENVELOPE = "envelope"


class OcrStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExtractStatus(StrEnum):
    RAW = "raw"
    NEED_CONFIRM = "need_confirm"
    CONFIRMED = "confirmed"


class InvoiceStatus(StrEnum):
    """票据级只有 5 个值；6.6 状态图里 signed/encrypted/... 属于报销单（D-011）。"""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    IN_REPORT = "in_report"
    ARCHIVED = "archived"
    VOID = "void"


class QualityFlag(StrEnum):
    """不设 fail：不可识别由 ocr_status='failed' 表达（D-014）。"""

    OK = "ok"
    LOW = "low"


class TicketType(StrEnum):
    VAT_GENERAL = "vat_general"
    # 数电票：D-023 的主流形态；S1 已有 layout 模板，S2-8 补进契约（D-046）
    VAT_DIGITAL = "vat_digital"
    TRAIN_TICKET = "train_ticket"
    FLIGHT_ITINERARY = "flight_itinerary"
    TAXI_TICKET = "taxi_ticket"
    UNKNOWN = "unknown"


class FieldSource(StrEnum):
    """ocr_retry 用于二次识别，必须能与首次识别区分（SCHEMA §9.8）。"""

    OCR = "ocr"
    RULE = "rule"
    SEMANTIC = "semantic"
    MANUAL = "manual"
    OCR_RETRY = "ocr_retry"


class OcrStage(StrEnum):
    PREPROCESS = "preprocess"
    DETECT = "detect"
    RECOGNIZE = "recognize"
    EXTRACT = "extract"


class FieldValidator(StrEnum):
    AMOUNT = "amount"
    DATE = "date"
    TAX_NO = "tax_no"
    CODE_NO = "code_no"
    UPPERCASE_AMOUNT = "uppercase_amount"


class ReportStatus(StrEnum):
    DRAFT = "draft"
    PENDING_SIGN = "pending_sign"
    SIGNED = "signed"
    ENCRYPTED = "encrypted"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class DigestAlgo(StrEnum):
    SM3 = "SM3"
    SHA256 = "SHA256"


class SignatureAlgo(StrEnum):
    SM2 = "SM2"
    RSA_PSS = "RSA-PSS"


class SignatureStatus(StrEnum):
    ACTIVE = "active"
    VOID = "void"


class VerifyResult(StrEnum):
    """只放验签码；信封拆封码 mac_mismatch/bad_padding 没有对应列（SCHEMA §9 末注）。"""

    OK = "ok"
    DIGEST_MISMATCH = "digest_mismatch"
    BAD_SIGNATURE = "bad_signature"
    CERT_INVALID = "cert_invalid"


class EnvelopeKeyAlgo(StrEnum):
    SM2 = "SM2"
    RSA_OAEP = "RSA-OAEP"


class CipherAlgo(StrEnum):
    SM4_CBC_HMAC_SM3 = "SM4-CBC-HMAC-SM3"
    AES_256_GCM = "AES-256-GCM"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class AuditActorRole(StrEnum):
    """三角色快照 + system（定时锚定任务）。"""

    SUBMITTER = "submitter"
    REVIEWER = "reviewer"
    ADMIN = "admin"
    SYSTEM = "system"


class AuditAction(StrEnum):
    UPLOAD = "upload"
    CONFIRM_FIELD = "confirm_field"
    SIGN = "sign"
    SEAL = "seal"
    SUBMIT = "submit"
    VERIFY = "verify"
    DECRYPT = "decrypt"
    REVIEW = "review"
    REVOKE_CERT = "revoke_cert"
    # S2-2 补：登录失败 / 登出 / 越权（API.md §0 §1 要求审计这三类动作，D-040）
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    FORBIDDEN = "forbidden"


class AuditTargetType(StrEnum):
    INVOICE = "invoice"
    INVOICE_FIELD = "invoice_field"
    FORM = "form"
    REPORT_ITEM = "report_item"
    SIGNATURE = "signature"
    ENVELOPE = "envelope"
    CERTIFICATE = "certificate"
    KEY = "key"
    USER = "user"
    TEMPLATE = "template"
    EVAL_RUN = "eval_run"


def enum_values(enum_cls: type[StrEnum]) -> tuple[str, ...]:
    """按定义顺序返回取值；顺序即 CHECK 文本顺序（可复现）。"""
    return tuple(member.value for member in enum_cls)


def enum_check(table: str, column: str, enum_cls: type[StrEnum]) -> CheckConstraint:
    """生成 `<列> IN ('a','b',...)` 的 CHECK，约束名 `ck_<表>_<列>`。

    取值来自枚举本身，所以"加一个枚举值却忘了改约束"在模型层不可能发生；
    迁移是生成当时的快照，改枚举值必须同时生成新迁移。
    """
    allowed = ", ".join(f"'{value}'" for value in enum_values(enum_cls))
    return CheckConstraint(
        f"{column} IN ({allowed})",
        name=f"ck_{table}_{column}",
    )