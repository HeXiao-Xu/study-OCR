"""OCR 能力层（AGENTS §10.3：对外只暴露函数 + Pydantic 模型）。"""
from app.ocr.detector import OcrContractError, OcrEngine, OcrOptions, get_engine, reset_engine
from app.ocr.preprocess import PreprocessOptions, PreprocessResult, QualityMetrics, preprocess
from app.ocr.recognizer import recognize_line, recognize_lines
from app.ocr.types import OcrLine, OcrResult

__all__ = [
    "OcrContractError", "OcrEngine", "OcrOptions", "get_engine", "reset_engine",
    "OcrLine", "OcrResult",
    "PreprocessOptions", "PreprocessResult", "QualityMetrics", "preprocess",
    "recognize_line", "recognize_lines",
]
