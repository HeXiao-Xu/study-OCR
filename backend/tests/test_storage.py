"""`storage.get_bytes` 单测（S3-1）：假 MinIO client，不连真实对象存储。

流水线要把对象读回内存再落临时文件，所以这里的三种失败必须分类清楚：
缺失（对象没了 → 不可重试）、不可用（MinIO 挂了 → 可重试）、超限（配置不一致）。
"""
from __future__ import annotations

import io

import pytest
from minio.error import S3Error

from app.core.exceptions import NotFoundError, PayloadTooLargeError, ServiceUnavailableError
from app.services.storage import StorageService

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._buffer = io.BytesIO(payload)
        self.closed = False
        self.released = False

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeClient:
    def __init__(self, payload: bytes | None = None, error: S3Error | None = None) -> None:
        self.payload = payload or b""
        self.error = error
        self.last_response: FakeResponse | None = None

    def get_object(self, bucket: str, key: str) -> FakeResponse:
        if self.error is not None:
            raise self.error
        self.last_response = FakeResponse(self.payload)
        return self.last_response


def _s3_error(code: str) -> S3Error:
    # S3Error(response, code, message, resource, request_id, host_id)
    return S3Error(None, code, "boom", None, None, None)


def test_get_bytes_returns_payload_and_releases_connection() -> None:
    client = FakeClient(b"hello")
    storage = StorageService(client, "bucket")

    assert storage.get_bytes("invoices/2026/11/a.bin", max_bytes=1024) == b"hello"
    assert client.last_response is not None
    assert client.last_response.closed and client.last_response.released


def test_get_bytes_missing_object_raises_not_found() -> None:
    storage = StorageService(FakeClient(error=_s3_error("NoSuchKey")), "bucket")

    with pytest.raises(NotFoundError):
        storage.get_bytes("invoices/2026/11/missing.bin", max_bytes=1024)


def test_get_bytes_unavailable_raises_503() -> None:
    storage = StorageService(FakeClient(error=_s3_error("InternalError")), "bucket")

    with pytest.raises(ServiceUnavailableError):
        storage.get_bytes("invoices/2026/11/x.bin", max_bytes=1024)


def test_get_bytes_over_limit_raises_413() -> None:
    storage = StorageService(FakeClient(b"x" * 2048), "bucket")

    with pytest.raises(PayloadTooLargeError):
        storage.get_bytes("invoices/2026/11/big.bin", max_bytes=1024)