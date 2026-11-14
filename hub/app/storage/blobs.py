"""Content-addressed immutable blobs: two-phase write, deduplication, manifests.

A blob's address is the `sha256` of the original bytes. Promotion is `pending/<uuid>` -> `audio/<sha256>` (or
`covers/<sha256>`), with a `head` check before the DB row becomes `committed`. The track row and the blob row are
committed in the same transaction by the caller.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Blob, utcnow
from app.storage import backend

log = logging.getLogger(__name__)

AUDIO_KIND = "audio"
COVER_KIND = "cover"
COMMITTED = "committed"
PENDING = "pending"
DELETED = "deleted"

_PREFIX = {AUDIO_KIND: "audio", COVER_KIND: "covers"}
_READ_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> tuple[str, int]:
    """Stream the file once: `(sha256 of the original bytes, size)`."""
    h = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as f:
        while chunk := f.read(_READ_CHUNK):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def content_type_for(path: Path | str) -> str:
    ctype, _ = mimetypes.guess_type(str(path))
    return ctype or "application/octet-stream"


def object_key(sha: str, kind: str = AUDIO_KIND) -> str:
    return f"{_PREFIX.get(kind, kind)}/{sha}"


def manifest_key(sha: str, uid: str) -> str:
    return f"manifests/{sha}/{uid}.json"


def manifest_prefix(sha: str) -> str:
    return f"manifests/{sha}/"


def pending_key() -> str:
    return f"pending/{uuid.uuid4().hex}"


async def get_blob(s: AsyncSession, sha: str) -> Blob | None:
    return (await s.execute(select(Blob).where(Blob.sha256 == sha).limit(1))).scalar_one_or_none()


async def get_or_create_pending(s: AsyncSession, sha: str, size: int, content_type: str, kind: str) -> Blob:
    blob = await get_blob(s, sha)
    if blob is None:
        blob = Blob(
            sha256=sha,
            size=size,
            content_type=content_type,
            kind=kind,
            status=PENDING,
            manifest_key=manifest_prefix(sha) if kind == AUDIO_KIND else None,
        )
        s.add(blob)
        await s.flush()
    return blob


async def store(
    s: AsyncSession,
    data: bytes | Path,
    *,
    kind: str = AUDIO_KIND,
    content_type: str | None = None,
    metadata: dict[str, str] | None = None,
    sha256: str | None = None,
    size: int | None = None,
) -> Blob:
    """Idempotently store `data` under its content address and return a committed blob row.

    The row is flushed, not committed: the caller commits it together with the track/album row.
    """
    if sha256 is None or size is None:
        if isinstance(data, Path):
            sha256, size = await asyncio.to_thread(sha256_file, data)
        else:
            sha256 = hashlib.sha256(data).hexdigest()
            size = len(data)
    assert sha256 is not None and size is not None
    ctype = content_type or (content_type_for(data) if isinstance(data, Path) else "application/octet-stream")

    blob = await get_or_create_pending(s, sha256, size, ctype, kind)
    if blob.status == COMMITTED:
        return blob
    if blob.size != size:
        raise ValueError(f"blob {sha256}: size mismatch ({blob.size} != {size})")

    b = backend()
    target = object_key(sha256, kind)
    tmp = pending_key()
    try:
        await b.put(tmp, data, content_type=ctype, metadata=metadata)
        info = await b.head(tmp)
        if info is None or info.size != size:
            raise OSError(f"{tmp}: uploaded {info.size if info else 'nothing'}, expected {size}")
        await b.copy(tmp, target)
        promoted = await b.head(target)
        if promoted is None or promoted.size != size:
            raise OSError(f"{target}: promoted object size {promoted.size if promoted else 'missing'}")
    finally:
        await b.delete(tmp)

    blob.status = COMMITTED
    blob.committed_at = utcnow()
    blob.verified_at = utcnow()
    blob.manifest_key = manifest_prefix(sha256) if kind == AUDIO_KIND else blob.manifest_key
    await s.flush()
    log.info("blob %s committed (%s, %d bytes)", sha256[:12], kind, size)
    return blob


async def store_audio(
    s: AsyncSession,
    path: Path,
    *,
    metadata: dict[str, str],
    sha256: str | None = None,
    size: int | None = None,
) -> Blob:
    return await store(
        s,
        path,
        kind=AUDIO_KIND,
        content_type=content_type_for(path),
        metadata=metadata,
        sha256=sha256,
        size=size,
    )


async def store_cover(s: AsyncSession, data: bytes) -> Blob:
    return await store(s, data, kind=COVER_KIND, content_type="image/jpeg")


async def write_manifest(sha256: str, uid: str, payload: dict[str, Any]) -> str:
    """One manifest per track (`manifests/<sha256>/<uid>.json`): a shared blob restores every track."""
    key = manifest_key(sha256, uid)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    await backend().put(key, body, content_type="application/json")
    return key


async def read_manifest(sha256: str, uid: str) -> dict[str, Any] | None:
    try:
        raw = await backend().get(manifest_key(sha256, uid))
    except Exception:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def read_bytes(blob: Blob) -> Any:
    """Fetch whole object bytes (covers; audio goes through the hot cache)."""
    return backend().get(object_key(blob.sha256, blob.kind))


async def audio_path(blob: Blob) -> Path:
    """A complete local copy of an audio blob (the hot cache fills it on a miss)."""
    from app.storage import cache

    return await cache().ensure(backend(), blob.sha256, expected_size=blob.size)
