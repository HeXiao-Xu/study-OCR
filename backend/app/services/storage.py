"""MinIO 对象存储封装（S2-6 / S3-1）。

只暴露 `put/get_bytes/delete/exists` 与对象名生成；业务层（`upload_service` / `ocr_pipeline`）
负责校验、去重与落库。对象名按 SCHEMA §3.1：`invoices/{yyyy}/{mm}/{uuid}.bin`——**不用原文件名、
不用 sha256 前缀**，避免路径穿越与"对象名泄露票据哈希"。
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, PayloadTooLargeError, ServiceUnavailableError

logger = logging.getLogger("app.storage")

_CHUNK_SIZE = 1024 * 1024


def build_object_key(now: datetime | None = None) -> str:
    stamp = now or datetime.now(timezone.utc)
    return f"invoices/{stamp:%Y}/{stamp:%m}/{secrets.token_hex(16)}.bin"


class StorageService:
    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def build_object_key(self) -> str:
        return build_object_key()

    def put(self, key: str, fileobj: BinaryIO, length: int, content_type: str) -> None:
        try:
            self._client.put_object(
                self._bucket, key, fileobj, length, content_type=content_type
            )
        except S3Error as exc:
            logger.error(
                "对象存储写入失败",
                extra={"event": "storage_put_failed", "error_code": "STORAGE_PUT"},
            )
            raise ServiceUnavailableError("对象存储不可用") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.remove_object(self._bucket, key)
        except S3Error as exc:
            logger.warning(
                "对象存储删除失败",
                extra={"event": "storage_delete_failed", "error_code": "STORAGE_DELETE"},
            )
            raise ServiceUnavailableError("对象存储不可用") from exc

    def exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NoSuchBucket"):
                return False
            raise ServiceUnavailableError("对象存储不可用") from exc

    def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        """读回对象完整字节（S3-1 识别流水线用）。

        缺失 → `NotFoundError`（永久失败，重试也没用）；不可用 → `ServiceUnavailableError`
        （可重试）；超过 `max_bytes` → `PayloadTooLargeError`（配置与对象不一致，必须人工看）。
        失败分类在这里一次做清，流水线不猜。
        """
        try:
            response = self._client.get_object(self._bucket, key)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NoSuchBucket"):
                raise NotFoundError("票据文件对象不存在") from exc
            raise ServiceUnavailableError("对象存储不可用") from exc

        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PayloadTooLargeError("票据文件超过读取上限")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            # 不论成败都要把连接还回池子，否则长时间跑批会把连接耗尽
            response.close()
            response.release_conn()


@lru_cache(maxsize=1)
def get_storage() -> StorageService:
    settings = get_settings()
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    return StorageService(client, settings.minio_bucket)