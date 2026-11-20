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

__all__ = [
    "PHOTO_GUIDE",
    "PreparedInput",
    "PreparedPage",
    "detect_input_kind",
    "prepare_input",
    "prepare_pages",
]

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


@dataclass
class PreparedPage:
    """一页待识别的图（S3-1 多页入口）。

    `quality_flag` 对 PDF 恒为 None：电子件不做预处理，也就没有实测质量，
    不写 `ok` 假装"质量检查过"。
    """

    page_no: int
    image: np.ndarray
    kind: str                      # "pdf" | "photo"
    quality_flag: str | None
    needs_retake: bool = False
    retake_hints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    preprocess: PreprocessResult | None = None


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


def _render_pdf(path: Path, dpi: int, max_pages: int | None = None
                ) -> tuple[list[np.ndarray], int]:
    """渲染 PDF 的前 `max_pages` 页（None = 全部），返回 (页图列表, 总页数)。"""
    try:
        import pymupdf as fitz          # PyMuPDF ≥2.1 的新门面名
    except ImportError:                 # 老版本仍叫 fitz
        import fitz

    images: list[np.ndarray] = []
    with fitz.open(str(path)) as document:
        page_count = document.page_count
        if page_count == 0:
            raise ValueError(f"PDF 没有页面：{path}")
        limit = page_count if max_pages is None else min(page_count, max_pages)
        for index in range(limit):
            page = document.load_page(index)
            pixmap = page.get_pixmap(dpi=dpi)
            array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            if pixmap.n == 4:
                image = array[:, :, :3][:, :, ::-1].copy()      # 去掉 alpha，RGBA → BGR
            elif pixmap.n == 3:
                image = array[:, :, ::-1].copy()                # RGB → BGR
            else:
                import cv2

                image = cv2.cvtColor(array[:, :, 0], cv2.COLOR_GRAY2BGR)
            images.append(image)
    return images, page_count


def prepare_input(source: str | Path | bytes, options: PreprocessOptions | None = None,
                  pdf_dpi: int = DEFAULT_PDF_DPI) -> PreparedInput:
    """PDF → 只渲染首页；照片 → 预处理 + 重拍判断。"""
    kind = detect_input_kind(source)

    if kind == "pdf":
        if isinstance(source, (bytes, bytearray)):
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(bytes(source))
                temp_path = Path(handle.name)
            try:
                images, pages = _render_pdf(temp_path, pdf_dpi, max_pages=1)
            finally:
                temp_path.unlink(missing_ok=True)
        else:
            images, pages = _render_pdf(Path(source), pdf_dpi, max_pages=1)
        return PreparedInput(image=images[0], kind="pdf", preprocessed=False, page_count=pages,
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


def prepare_pages(source: str | Path | bytes, options: PreprocessOptions | None = None,
                  *, max_pages: int = 3, pdf_dpi: int = DEFAULT_PDF_DPI) -> list[PreparedPage]:
    """S3-1 的多页入口：PDF 只渲染前 `max_pages` 页（不做预处理，D-035）；照片预处理后单页。

    任务卡写"限前 3 页"——票据 PDF 极少超过 1 页，限页是防超大文件把单任务拖过 180s。
    """
    kind = detect_input_kind(source)

    if kind == "photo":
        prepared = prepare_input(source, options)
        quality = prepared.preprocess.quality_flag if prepared.preprocess is not None else None
        return [
            PreparedPage(
                page_no=1,
                image=prepared.image,
                kind="photo",
                quality_flag=quality,
                needs_retake=prepared.needs_retake,
                retake_hints=prepared.retake_hints,
                notes=prepared.notes,
                preprocess=prepared.preprocess,
            )
        ]

    if isinstance(source, (bytes, bytearray)):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(bytes(source))
            temp_path = Path(handle.name)
        try:
            images, _ = _render_pdf(temp_path, pdf_dpi, max_pages=max_pages)
        finally:
            temp_path.unlink(missing_ok=True)
    else:
        images, _ = _render_pdf(Path(source), pdf_dpi, max_pages=max_pages)

    return [
        PreparedPage(
            page_no=index + 1,
            image=image,
            kind="pdf",
            quality_flag=None,
            notes=[f"pdf_rendered:{pdf_dpi}dpi",
                   "pdf_skip_preprocess:电子件本身端正，不做预处理（见 D-035）"],
        )
        for index, image in enumerate(images)
    ]
