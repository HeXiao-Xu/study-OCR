"""OCR 契约的兼容导出（S3-4 起唯一来源是 `app/schemas/ocr.py`）。

保留本模块是为了不破坏 `ocr/` 内部与外部既有 import；**不要在这里再定义模型**。
"""
from app.schemas.ocr import (
    ExtractedField,
    ExtractionResult,
    FieldError,
    OcrLine,
    OcrResult,
)

__all__ = ["ExtractedField", "ExtractionResult", "FieldError", "OcrLine", "OcrResult"]