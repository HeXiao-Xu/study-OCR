"""MinIO 对象存储封装（S2-6）。

只暴露 `put/delete/exists` 与对象名生成；业务层（`upload_service`）负责校验、去重与落库。
对象名按 SCHEMA §3.1：`invoices/{yyyy}/{mm}/{uuid}.bin`——**不用原文件名、不用 sha256 前缀**，
避免路径穿越与"对象名泄露票据哈希"。
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
from app.core.exceptions import ServiceUnavailableError

logger = logging.getLogger("app.storage")


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