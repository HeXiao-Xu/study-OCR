"""导出识别产物 JSON Schema（S3-4）。

    .venv/Scripts/python.exe scripts/export_ocr_schema.py           # 写 docs/schemas/ocr_result.schema.json
    .venv/Scripts/python.exe scripts/export_ocr_schema.py --check   # 只比对，不一致退出码 1（测试/CI 用）

为什么提交生成的 JSON：前端与评测脚本要对照字段名，没有文件就只能靠人读 Python 源码；
漂移由 `backend/tests/test_ocr_schema.py` 把关（重算后与文件深度比较）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)          # Settings 从 backend/.env 读，和其他脚本一致

from pydantic.json_schema import models_json_schema  # noqa: E402

from app.schemas.ocr import (  # noqa: E402
    ExtractedField,
    ExtractionResult,
    FieldError,
    OcrLine,
    OcrResult,
)

SCHEMA_PATH = ROOT / "docs" / "schemas" / "ocr_result.schema.json"
MODELS = {
    "OcrLine": OcrLine,
    "OcrResult": OcrResult,
    "ExtractedField": ExtractedField,
    "FieldError": FieldError,
    "ExtractionResult": ExtractionResult,
}


def build_schema() -> dict:
    # models_json_schema 返回 (definitions, top_level)；模型定义在 top_level["$defs"] 里
    _, top_level = models_json_schema(
        [(model, "validation") for model in MODELS.values()]
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://study-ocr.local/schemas/ocr_result.schema.json",
        "title": "OCR 识别产物契约（S3-4）",
        "description": (
            "OcrLine.box 为四点（校正图坐标）；"
            "ExtractedField.bbox 为 [x1,y1,x2,y2]（原图坐标）"
        ),
        "$defs": top_level.get("$defs", {}),
    }


def render(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 OCR 产物 JSON Schema")
    parser.add_argument("--check", action="store_true", help="只比对不写盘")
    args = parser.parse_args()

    rendered = render(build_schema())
    if args.check:
        if not SCHEMA_PATH.exists():
            print(f"缺少 {SCHEMA_PATH}", file=sys.stderr)
            return 1
        if SCHEMA_PATH.read_text(encoding="utf-8") != rendered:
            print(
                "JSON Schema 与模型不一致：请重新运行 scripts/export_ocr_schema.py",
                file=sys.stderr,
            )
            return 1
        print("schema OK")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(rendered, encoding="utf-8")
    print(f"已写入 {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())