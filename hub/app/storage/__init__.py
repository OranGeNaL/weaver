"""Storage access: the configured backend singleton and the blob hot cache."""

from __future__ import annotations

import logging
from typing import cast

from app.config import get_settings
from app.storage.base import (
    ObjectInfo,
    ObjectNotFound,
    PresignUnavailable,
    StorageBackend,
    StorageError,
)
from app.storage.cache import HotCache
from app.storage.local import LocalBackend
from app.storage.s3 import S3Backend

log = logging.getLogger(__name__)

_backend: StorageBackend | None = None
_cache: HotCache | None = None


def backend() -> StorageBackend:
    global _backend
    if _backend is None:
        s = get_settings()
        if s.storage_backend == "fs":
            s.validate_storage()
            _backend = cast(StorageBackend, LocalBackend(s.objects_dir))
        else:
            _backend = cast(
                StorageBackend,
                S3Backend(
                    endpoint=s.s3_endpoint,
                    bucket=s.s3_bucket,
                    region=s.s3_region,
                    access_key=s.s3_access_key,
                    secret_key=s.s3_secret_key,
                    path_style=s.s3_path_style,
                ),
            )
    return _backend


def cache() -> HotCache:
    global _cache
    if _cache is None:
        s = get_settings()
        _cache = HotCache(s.cache_dir, s.storage_cache_max_bytes)
    return _cache


async def startup() -> None:
    """Validate the backend, create the bucket or local root, enable versioning where supported."""
    s = get_settings()
    s.validate_storage()
    b = backend()
    if s.storage_backend == "fs":
        s.objects_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(b, S3Backend) and s.s3_create_bucket:
        # a temporarily unreachable bucket or a provider without CreateBucket must not stop the Hub: /healthz
        # reports storage as down and the operations that need bytes fail with a clear error
        try:
            await b.ensure_bucket()
        except Exception as e:
            log.warning("s3: bucket %s is not ready at startup: %r", s.s3_bucket, e)
        else:
            if s.s3_versioning:
                try:
                    if not await b.versioning():
                        log.info("s3: bucket versioning is not supported by the provider")
                except Exception as e:
                    log.info("s3: versioning could not be enabled: %r", e)
    if not await b.check():
        log.warning("storage backend is not available at startup; /healthz reports it as down")


async def status() -> dict[str, object]:
    """Health of the storage layer: bucket availability and local cache occupancy."""
    try:
        ok = await backend().check()
    except Exception as e:  # a misconfigured backend must not take /healthz down
        log.warning("storage health check failed: %r", e)
        ok = False
    return {
        "backend": get_settings().storage_backend,
        "available": ok,
        "cache_bytes": await _cache_bytes(),
        "cache_max_bytes": get_settings().storage_cache_max_bytes,
    }


async def _cache_bytes() -> int:
    import asyncio

    return await asyncio.to_thread(cache().total_bytes)


def reset() -> None:
    """Forget the configured backend and cache (tests, shutdown)."""
    global _backend, _cache
    if _cache is not None:
        _cache.reset()
    _backend = None
    _cache = None


__all__ = [
    "ObjectInfo",
    "ObjectNotFound",
    "PresignUnavailable",
    "StorageBackend",
    "StorageError",
    "backend",
    "cache",
    "reset",
    "startup",
    "status",
]
