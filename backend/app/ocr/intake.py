"""输入分流（S1 收口补强）：PDF 电子发票 vs 拍照件。

用户的要求很明确，也符合工程常识：

- **电子发票 PDF 不需要预处理**：它是税务端/系统导出的，本身不歪、不糊、没有桌面背景，
  对它做透视/照度校正只有损失（S1-6 的对照实验已经量到：预处理让检测 Hmean 从 0.974 掉到 0.948）。
  所以 PDF 只做"渲染成图"，不做 S1-2 的任何一步。
- **拍照件才需要预处理**，并且要**先给用户拍好的提示**；如果糊到看不清、或者歪到认不出边界，
  就明确告诉他"重拍"，而不是硬着头皮识别再给一堆错字段。

对外只暴露 `prepare_input()`：调用方（S3 的 Worker）不需要知道内部是 PDF 还是照片。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.ocr.preprocess import PreprocessOptions, PreprocessResult, preprocess

__all__ = ["PreparedInput", "detect_input_kind", "prepare_input", "PHOTO_GUIDE"]

PDF_MAGIC = b"%PDF"
DEFAULT_PDF_DPI = 250

# 拍照引导语：前端在上传前直接展示（比"上传后报错"友好得多）
PHOTO_GUIDE = [
    "把票据平铺在纯色桌面上，四角都要在画面里，四周留一点边距",
    "正对票据拍（尽量垂直俯拍），不要斜着拍",
    "光线均匀、避开反光和阴影；不要用闪光灯",
    "拍完看一眼：字要清晰、票据不要歪出画面",
]


@dataclass
class PreparedInput:
    image: np.ndarray
    kind: str                      # "pdf" | "photo"
    preprocessed: bool             # PDF 恒为 False（按设计不做预处理）
    needs_retake: bool = False
    retake_hints: list[str] = field(default_factory=list)
    page_count: int = 1
    preprocess: PreprocessResult | None = None
    notes: list[str] = field(default_factory=list)


def detect_input_kind(source: str | Path | bytes) -> str:
    """按魔数判断，而不是看扩展名（用户把 PDF 存成 .png 的情况很常见）。"""
    if isinstance(source, (bytes, bytearray)):
        return "pdf" if bytes(source[:5]).startswith(PDF_MAGIC) else "photo"
    path = Path(source)
    try:
        with path.open("rb") as handle:
            return "pdf" if handle.read(5).startswith(PDF_MAGIC) else "photo"
    except OSError:
        return "photo"


def _render_pdf(path: Path, dpi: int) -> tuple[np.ndarray, int]:
    try:
        import pymupdf as fitz          # PyMuPDF ≥2.1 的新门面名
    except ImportError:                 # 老版本仍叫 fitz
        import fitz

    with fitz.open(str(path)) as document:
        if document.page_count == 0:
            raise ValueError(f"PDF 没有页面：{path}")
        page = document.load_page(0)
        pixmap = page.get_pixmap(dpi=dpi)
        array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
        if pixmap.n == 4:
            image = array[:, :, :3][:, :, ::-1].copy()      # 去掉 alpha，RGBA → BGR
        elif pixmap.n == 3:
            image = array[:, :, ::-1].copy()                # RGB → BGR
        else:
            import cv2

            image = cv2.cvtColor(array[:, :, 0], cv2.COLOR_GRAY2BGR)
        return image, document.page_count


def prepare_input(source: str | Path | bytes, options: PreprocessOptions | None = None,
                  pdf_dpi: int = DEFAULT_PDF_DPI) -> PreparedInput:
    """PDF → 只渲染；照片 → 预处理 + 重拍判断。"""
    kind = detect_input_kind(source)

    if kind == "pdf":
        if isinstance(source, (bytes, bytearray)):
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(bytes(source))
                temp_path = Path(handle.name)
            try:
                image, pages = _render_pdf(temp_path, pdf_dpi)
            finally:
                temp_path.unlink(missing_ok=True)
        else:
            image, pages = _render_pdf(Path(source), pdf_dpi)
        return PreparedInput(image=image, kind="pdf", preprocessed=False, page_count=pages,
                             notes=[f"pdf_rendered:{pdf_dpi}dpi",
                                    "pdf_skip_preprocess:电子件本身端正，不做预处理（见 D-035）"])

    result = preprocess(source, options)
    return PreparedInput(
        image=result.image,
        kind="photo",
        preprocessed=True,
        needs_retake=result.needs_retake,
        retake_hints=result.retake_hints,
        preprocess=result,
        notes=[f"rectify:{result.steps['rectify']}", f"quality:{result.quality_flag}"],
    )
