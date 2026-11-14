"""Album covers as blobs: resized variants cached locally by `(sha256, size)`."""

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.library import tagger
from app.models import Album, Track
from app.storage import backend, blobs, cache


def _resize(data: bytes, size: int) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im.thumbnail((size, size))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()


def _cache_path(sha: str, size: int) -> Path:
    return get_settings().data_dir / "cache" / "covers" / f"{sha}-{size}.jpg"


async def _first_track_cover(s: AsyncSession, album_id: int) -> tuple[str, bytes] | None:
    rows = (
        await s.execute(
            select(Track.blob_id)
            .where(Track.album_id == album_id, Track.blob_id.is_not(None), Track.deleted_at.is_(None))
            .order_by(Track.disc_no, Track.track_no, Track.id)
        )
    ).scalars()
    for blob_id in rows:
        blob = await s.get(blobs.Blob, blob_id)
        if blob is None:
            continue
        path = await blobs.audio_path(blob)
        data = await asyncio.to_thread(tagger.read_cover, path)
        if data:
            return hashlib.sha256(data).hexdigest(), data
    return None


async def cover_bytes(s: AsyncSession, album: Album) -> tuple[str, bytes] | None:
    """`(sha256, raw cover bytes)`: the album's cover blob, else a cover embedded in one of its audio blobs."""
    if album.cover_blob_id is not None:
        blob = await s.get(blobs.Blob, album.cover_blob_id)
        if blob is not None and blob.status != blobs.PENDING:
            return blob.sha256, await backend().get(blobs.object_key(blob.sha256, blob.kind))
    return await _first_track_cover(s, album.id)


def reset() -> None:
    """No in-memory state; kept for symmetry with other cache modules."""
    return None


async def cached_or_resized(s: AsyncSession, album: Album, size: int | None) -> bytes | None:
    found = await cover_bytes(s, album)
    if found is None:
        return None
    sha, data = found
    if size is None:
        return data
    dest = _cache_path(sha, size)
    if dest.is_file():
        await asyncio.to_thread(dest.touch)
        return await asyncio.to_thread(dest.read_bytes)
    resized = await asyncio.to_thread(_resize, data, size)
    dest.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(dest.write_bytes, resized)
    return resized


__all__ = ["cache", "cached_or_resized", "cover_bytes", "reset"]
