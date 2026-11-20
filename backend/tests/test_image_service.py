"""S3-5 影像渲染：原图直出 / PDF 按 250DPI 渲染 / 预处理变体 / 明文闸门。"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.exceptions import ConflictError, ValidationFailedError
from app.models.invoice_files import InvoiceFile
from app.services import image_service

_BASE = dict(
    database_url="postgresql://u:p@127.0.0.1:5433/db",
    redis_url="redis://:p@127.0.0.1:6380/0",
    minio_endpoint="127.0.0.1:9002", minio_access_key="k", minio_secret_key="s",
    minio_bucket="b", jwt_secret="s" * 32,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_BASE, **overrides})


def _jpeg(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _pdf(pages: int = 2) -> bytes:
    import pymupdf

    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page(width=595, height=842)      # A4 pt
        page.insert_text((72, 100), f"page {index + 1}")
    return document.tobytes()


def _file(mime: str, *, pages: int = 1, cipher: bool = False) -> InvoiceFile:
    return InvoiceFile(
        uploader_id=1, filename="a.bin", mime=mime, size=1, sha256="0" * 64,
        storage_key="invoices/x.bin", is_cipher=cipher, page_count=pages,
    )


def test_image_bytes_returned_as_is_for_jpeg() -> None:
    payload = _jpeg()

    rendered = image_service.render_image(
        _file("image/jpeg"), payload, page=1, variant="original", settings=_settings()
    )

    assert rendered.content == payload
    assert rendered.media_type == "image/jpeg"
    assert len(rendered.etag) == 64


def test_pdf_page_rendered_at_250_dpi() -> None:
    rendered = image_service.render_image(
        _file("application/pdf", pages=2), _pdf(2), page=2, variant="original",
        settings=_settings(),
    )

    assert rendered.media_type == "image/png"
    with Image.open(io.BytesIO(rendered.content)) as decoded:
        # A4 595×842pt @250dpi ≈ 2066×2924
        assert abs(decoded.width - 2066) <= 3
        assert abs(decoded.height - 2924) <= 3


def test_page_out_of_range_rejected() -> None:
    with pytest.raises(ValidationFailedError):
        image_service.render_image(
            _file("application/pdf", pages=2), _pdf(2), page=3, variant="original",
            settings=_settings(),
        )


def test_cipher_file_rejected() -> None:
    with pytest.raises(ConflictError):
        image_service.render_image(
            _file("image/jpeg", cipher=True), _jpeg(), page=1, variant="original",
            settings=_settings(),
        )


def test_plaintext_disabled_rejected() -> None:
    with pytest.raises(ConflictError):
        image_service.render_image(
            _file("image/jpeg"), _jpeg(), page=1, variant="original",
            settings=_settings(storage_allow_plaintext=False),
        )


def test_preprocessed_variant_returns_png() -> None:
    rendered = image_service.render_image(
        _file("image/jpeg"), _jpeg(160, 120), page=1, variant="preprocessed",
        settings=_settings(),
    )

    assert rendered.media_type == "image/png"
    with Image.open(io.BytesIO(rendered.content)) as decoded:
        assert decoded.format == "PNG"


def test_invalid_variant_rejected() -> None:
    with pytest.raises(ValidationFailedError):
        image_service.render_image(
            _file("image/jpeg"), _jpeg(), page=1, variant="nope", settings=_settings(),
        )