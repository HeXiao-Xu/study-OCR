"""输入分流与"重拍"判断的单测（S1 收口补强）。

两块逻辑一旦错，用户体验会很难受：
- PDF 被判成照片 → 白白做一遍预处理，反而让原本清晰的电子票变差；
- 照片质量不够却不提示 → 用户拿到一堆错字段，还以为系统不行。
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from app.ocr.intake import (
    PHOTO_GUIDE,
    PreparedInput,
    detect_input_kind,
    prepare_input,
    prepare_pages,
)
from app.ocr.preprocess import PreprocessOptions, preprocess


def _write_pdf(path, text: str = "测试发票") -> None:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        pytest.skip("PyMuPDF 不可用")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), text)
    document.save(str(path))
    document.close()


def _write_photo(path, *, blur: float = 0.0, size=(600, 900)) -> None:
    image = Image.new("RGB", size, (245, 245, 245))
    for x in range(40, size[0] - 40, 7):
        for y in range(60, size[1] - 60, 11):
            image.putpixel((x, y), (30, 30, 30))
    if blur > 0:
        from PIL import ImageFilter

        image = image.filter(ImageFilter.GaussianBlur(radius=blur))
    image.save(path, quality=90)


def test_detect_pdf_by_magic_bytes(tmp_path) -> None:
    pdf = tmp_path / "invoice.png"          # 故意用错扩展名
    _write_pdf(pdf)
    assert detect_input_kind(pdf) == "pdf"


def test_detect_photo(tmp_path) -> None:
    photo = tmp_path / "shot.jpg"
    _write_photo(photo)
    assert detect_input_kind(photo) == "photo"


def test_pdf_is_rendered_without_preprocessing(tmp_path) -> None:
    pdf = tmp_path / "e_invoice.pdf"
    _write_pdf(pdf)

    prepared = prepare_input(pdf)
    assert prepared.kind == "pdf"
    assert prepared.preprocessed is False
    assert prepared.needs_retake is False
    assert prepared.image.ndim == 3 and prepared.image.size > 0
    assert any("pdf_skip_preprocess" in note for note in prepared.notes)


def test_photo_goes_through_preprocess(tmp_path) -> None:
    photo = tmp_path / "shot.jpg"
    _write_photo(photo)

    prepared = prepare_input(photo, PreprocessOptions(illum=False, detect_seal=False))
    assert prepared.kind == "photo"
    assert prepared.preprocessed is True
    assert prepared.preprocess is not None
    assert isinstance(prepared.needs_retake, bool)


def test_blurry_photo_asks_for_retake(tmp_path) -> None:
    photo = tmp_path / "blurry.jpg"
    _write_photo(photo, blur=6.0)

    result = preprocess(photo, PreprocessOptions(illum=False, detect_seal=False))
    assert result.needs_retake is True
    assert any("模糊" in hint or "重拍" in hint for hint in result.retake_hints)


def test_clean_photo_has_no_retake_hint(tmp_path) -> None:
    photo = tmp_path / "clean.jpg"
    _write_photo(photo, size=(900, 1200))

    result = preprocess(photo, PreprocessOptions(illum=False, detect_seal=False))
    # 干净图不应当被判"重拍"（可能因边缘贴边命中，这里只断言不因清晰度触发）
    assert not any("模糊" in hint for hint in result.retake_hints)


def test_photo_guide_is_actionable() -> None:
    assert len(PHOTO_GUIDE) >= 3
    assert "票" in "".join(PHOTO_GUIDE)
    assert any("边距" in line for line in PHOTO_GUIDE)


def test_prepared_input_defaults() -> None:
    prepared = PreparedInput(image=np.zeros((4, 4, 3), dtype=np.uint8), kind="photo", preprocessed=True)
    assert prepared.needs_retake is False and prepared.retake_hints == []


# --------------------------------------------------------------------------- #
# S3-1：多页入口（PDF 限前 N 页；照片单页）
# --------------------------------------------------------------------------- #
def _write_multipage_pdf(path, pages: int = 3) -> None:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        pytest.skip("PyMuPDF 不可用")
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 100), f"第 {index + 1} 页")
    document.save(str(path))
    document.close()


def test_prepare_pages_pdf_returns_all_pages(tmp_path) -> None:
    pdf = tmp_path / "multi.pdf"
    _write_multipage_pdf(pdf, pages=3)

    pages = prepare_pages(pdf)
    assert [page.page_no for page in pages] == [1, 2, 3]
    assert all(page.kind == "pdf" for page in pages)
    assert all(page.image.ndim == 3 and page.image.size > 0 for page in pages)
    assert all(page.quality_flag is None for page in pages)      # PDF 不预处理，不编造质量结论


def test_prepare_pages_pdf_caps_max_pages(tmp_path) -> None:
    pdf = tmp_path / "multi.pdf"
    _write_multipage_pdf(pdf, pages=3)

    pages = prepare_pages(pdf, max_pages=2)
    assert [page.page_no for page in pages] == [1, 2]


def test_prepare_pages_photo_returns_single_page(tmp_path) -> None:
    photo = tmp_path / "shot.jpg"
    _write_photo(photo)

    pages = prepare_pages(photo, PreprocessOptions(illum=False, detect_seal=False))
    assert len(pages) == 1
    assert pages[0].page_no == 1
    assert pages[0].kind == "photo"
    assert pages[0].quality_flag in {"ok", "low"}
    assert pages[0].preprocess is not None


def test_prepare_pages_pdf_from_bytes(tmp_path) -> None:
    pdf = tmp_path / "multi.pdf"
    _write_multipage_pdf(pdf, pages=2)

    pages = prepare_pages(pdf.read_bytes(), max_pages=3)
    assert [page.page_no for page in pages] == [1, 2]
