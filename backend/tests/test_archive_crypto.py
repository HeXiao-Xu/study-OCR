"""S5-6 归档包口令加密：往返 / 错口令 / 篡改 / 坏头（默认套件）。"""
from __future__ import annotations

import pytest

from app.crypto.archive_crypto import (
    MAGIC,
    ArchiveOpenError,
    open_archive,
    seal_archive,
)

PAYLOAD = b"PK\x03\x04fake-zip-bytes-for-test" * 10
PASS = "archive-pass-1234"


def test_archive_roundtrip_returns_original_bytes() -> None:
    blob = seal_archive(PAYLOAD, PASS)
    assert blob.startswith(MAGIC)
    assert open_archive(blob, PASS) == PAYLOAD


def test_archive_wrong_passphrase_fails_uniformly() -> None:
    blob = seal_archive(PAYLOAD, PASS)
    with pytest.raises(ArchiveOpenError) as exc:
        open_archive(blob, "wrong-pass-0000")
    assert str(exc.value) == "口令错误或归档包损坏"


def test_archive_one_bit_tamper_fails() -> None:
    blob = bytearray(seal_archive(PAYLOAD, PASS))
    blob[-1] ^= 0x01
    with pytest.raises(ArchiveOpenError):
        open_archive(bytes(blob), PASS)


def test_archive_truncated_or_bad_header_fails() -> None:
    blob = seal_archive(PAYLOAD, PASS)
    for bad in (blob[:20], b"NOT-A-MAGIC" + blob[16:], b""):
        with pytest.raises(ArchiveOpenError):
            open_archive(bad, PASS)


def test_archive_rejects_short_passphrase() -> None:
    with pytest.raises(ValueError):
        seal_archive(PAYLOAD, "short")