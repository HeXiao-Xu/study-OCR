"""S5-4/S5-7：财务预览的 PDF 影像必须渲染成 PNG（浏览器 <img> 不能直接显示 PDF）。"""
from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.services.credential_store import InMemoryCredentialStore, preview_key
from app.services.preview_service import read_preview_file


class _Storage:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def get_bytes(self, _key: str, *, max_bytes: int) -> bytes:
        assert len(self.data) <= max_bytes
        return self.data


def _pdf_bytes() -> bytes:
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "invoice")
    data = document.tobytes()
    document.close()
    return data


def _store(token: str, mime: str) -> InMemoryCredentialStore:
    store = InMemoryCredentialStore()
    store.put(preview_key(token), json.dumps({"report_id": 1, "files": {"1": mime}}), 60)
    return store


def test_preview_pdf_is_rendered_to_png() -> None:
    token = "pdf-token"
    content, mime = read_preview_file(
        _store(token, "application/pdf"),
        _Storage(_pdf_bytes()),
        token,
        1,
        settings=get_settings(),
    )
    assert mime == "image/png"
    assert content.startswith(b"\x89PNG\r\n\x1a\n")


def test_preview_image_passes_through_unchanged() -> None:
    token = "img-token"
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    content, mime = read_preview_file(
        _store(token, "image/jpeg"),
        _Storage(jpeg),
        token,
        1,
        settings=get_settings(),
    )
    assert mime == "image/jpeg"
    assert content == jpeg


def test_preview_missing_file_is_404() -> None:
    from app.core.exceptions import NotFoundError

    store = InMemoryCredentialStore()
    store.put(preview_key("x"), json.dumps({"report_id": 1, "files": {}}), 60)
    with pytest.raises(NotFoundError):
        read_preview_file(store, _Storage(b"x"), "x", 1, settings=get_settings())