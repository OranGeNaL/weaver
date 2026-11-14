"""S3-compatible backend (RustFS in compose, or an external S3). `boto3` runs in a worker thread."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.storage.base import ObjectInfo, ObjectNotFound, StorageError

_CHUNK = 256 * 1024


class S3Backend:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        path_style: bool = True,
    ):
        self.endpoint = endpoint
        self.bucket = bucket
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.path_style = path_style
        self._client: Any = None
        self._lock = threading.Lock()

    def _make_client(self) -> Any:
        # imported lazily: the fs backend and the tests must work without boto3 installed
        import importlib

        boto3 = importlib.import_module("boto3")
        config_mod = importlib.import_module("botocore.config")

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint or None,
            region_name=self.region or None,
            aws_access_key_id=self.access_key or None,
            aws_secret_access_key=self.secret_key or None,
            config=config_mod.Config(s3={"addressing_style": "path" if self.path_style else "virtual"}),
        )

    def client(self) -> Any:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = self._make_client()
        return self._client

    @staticmethod
    def _quote_metadata(metadata: dict[str, str] | None) -> dict[str, str]:
        # S3 user metadata is ASCII-only; values are percent-encoded so Cyrillic survives the round trip
        return {str(k): quote(str(v), safe="") for k, v in (metadata or {}).items()}

    @staticmethod
    def _unquote_metadata(raw: dict[str, str] | None) -> dict[str, str]:
        from urllib.parse import unquote

        return {str(k): unquote(str(v)) for k, v in (raw or {}).items()}

    async def put(
        self,
        key: str,
        data: bytes | Path,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectInfo:
        extra: dict[str, Any] = {"Metadata": self._quote_metadata(metadata)}
        if content_type:
            extra["ContentType"] = content_type

        def upload() -> ObjectInfo:
            if isinstance(data, Path):
                self.client().upload_file(str(data), self.bucket, key, ExtraArgs=extra)
                size = data.stat().st_size
            else:
                self.client().put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
                size = len(data)
            return ObjectInfo(key=key, size=size, content_type=content_type, metadata=dict(metadata or {}))

        return await asyncio.to_thread(upload)

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            try:
                obj = self.client().get_object(Bucket=self.bucket, Key=key)
            except self.client().exceptions.NoSuchKey as e:
                raise ObjectNotFound(key) from e
            return obj["Body"].read()

        return await asyncio.to_thread(read)

    async def get_range(self, key: str, start: int, end: int | None = None) -> bytes:
        def read() -> bytes:
            rng = f"bytes={start}-{'' if end is None else end}"
            try:
                obj = self.client().get_object(Bucket=self.bucket, Key=key, Range=rng)
            except self.client().exceptions.NoSuchKey as e:
                raise ObjectNotFound(key) from e
            return obj["Body"].read()

        return await asyncio.to_thread(read)

    async def iter_range(self, key: str, start: int = 0, end: int | None = None) -> AsyncIterator[bytes]:
        rng = f"bytes={start}-{'' if end is None else end}"
        try:
            obj = await asyncio.to_thread(self.client().get_object, Bucket=self.bucket, Key=key, Range=rng)
        except Exception as e:  # botocore error types are not importable without the dependency
            if type(e).__name__ in ("NoSuchKey", "ClientError") and "NoSuchKey" in repr(e):
                raise ObjectNotFound(key) from e
            raise
        body = obj["Body"]
        try:
            while True:
                chunk = await asyncio.to_thread(body.read, _CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(body.close)

    async def head(self, key: str) -> ObjectInfo | None:
        def info() -> ObjectInfo | None:
            try:
                obj = self.client().head_object(Bucket=self.bucket, Key=key)
            except Exception as e:
                if type(e).__name__ in ("ClientError", "NoSuchKey") and "404" in repr(e):
                    return None
                if type(e).__name__ == "NoSuchKey":
                    return None
                raise
            return ObjectInfo(
                key=key,
                size=int(obj.get("ContentLength") or 0),
                content_type=obj.get("ContentType"),
                etag=str(obj.get("ETag") or "").strip('"') or None,
                metadata=self._unquote_metadata(obj.get("Metadata")),
            )

        return await asyncio.to_thread(info)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client().delete_object, Bucket=self.bucket, Key=key)

    async def presign_get(self, key: str, expires_s: int = 3600) -> str:
        return await asyncio.to_thread(
            self.client().generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_s,
        )

    async def list(self, prefix: str = "") -> list[str]:
        def walk() -> list[str]:
            out: list[str] = []
            paginator = self.client().get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                out.extend(item["Key"] for item in page.get("Contents", []))
            return out

        return await asyncio.to_thread(walk)

    async def list_aged(self, prefix: str = "") -> list[tuple[str, float]]:
        """Object keys with their LastModified (seconds since epoch), for GC's TTLs."""

        def walk() -> list[tuple[str, float]]:
            out: list[tuple[str, float]] = []
            paginator = self.client().get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    modified = item.get("LastModified")
                    out.append((item["Key"], modified.timestamp() if modified else 0.0))
            return out

        return await asyncio.to_thread(walk)

    async def copy(self, src: str, dst: str) -> None:
        await asyncio.to_thread(
            self.client().copy_object,
            Bucket=self.bucket,
            CopySource={"Bucket": self.bucket, "Key": src},
            Key=dst,
        )

    async def ensure_bucket(self) -> None:
        def create() -> None:
            try:
                self.client().head_bucket(Bucket=self.bucket)
            except Exception:
                self.client().create_bucket(Bucket=self.bucket)

        await asyncio.to_thread(create)

    async def check(self) -> bool:
        def probe() -> bool:
            try:
                self.client().head_bucket(Bucket=self.bucket)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(probe)

    async def versioning(self) -> bool:
        """Turn bucket versioning on when the provider supports it; False means it is unavailable."""

        def enable() -> bool:
            try:
                self.client().put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"})
                return True
            except Exception:
                return False

        return await asyncio.to_thread(enable)


__all__ = ["ObjectNotFound", "S3Backend", "StorageError"]
