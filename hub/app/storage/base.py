"""The storage seam (`storage.backend`): one contract for local files and S3-compatible buckets.

The library lives in an object store; the local disk keeps only staging, the hot cache and temp files.
Objects are immutable and addressed by content (`audio/<sha256>`, `covers/<sha256>`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class StorageError(Exception):
    """The storage backend could not fulfil the operation."""


class ObjectNotFound(StorageError):
    """There is no object under this key."""


class PresignUnavailable(StorageError):
    """The backend cannot issue a presigned URL (a local directory)."""


@dataclass(slots=True)
class ObjectInfo:
    key: str
    size: int
    content_type: str | None = None
    etag: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class StorageBackend(Protocol):
    """What the Hub needs from a store: put/get/get_range/head/delete/presign_get/list.

    `copy` and `iter_range` are conveniences the two built-in backends share (promotion of a pending object and
    streaming a Range without loading it into memory).
    """

    async def put(
        self,
        key: str,
        data: bytes | Path,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectInfo: ...

    async def get(self, key: str) -> bytes: ...

    async def get_range(self, key: str, start: int, end: int | None = None) -> bytes: ...

    def iter_range(self, key: str, start: int = 0, end: int | None = None) -> AsyncIterator[bytes]: ...

    async def head(self, key: str) -> ObjectInfo | None: ...

    async def delete(self, key: str) -> None: ...

    async def presign_get(self, key: str, expires_s: int = 3600) -> str: ...

    async def list(self, prefix: str = "") -> list[str]: ...

    async def copy(self, src: str, dst: str) -> None: ...

    async def check(self) -> bool: ...
