"""Materialization/export: a temporary tagged copy of an immutable blob.

Ordinary edits never rewrite the object: tags are written only here, on a temp copy, when a user downloads or an
export is requested. The blob stays byte-identical, so `sha256` and deduplication hold.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.library import covers, tagger
from app.library.importer import tagdata_for
from app.models import Album, Track
from app.storage import blobs


async def materialize(s: AsyncSession, track: Track) -> Path:
    """A temp file with the track's tags: blob bytes -> staging copy -> `write_tags` -> caller serves/removes it."""
    if track.blob_id is None:
        raise ValueError("track has no audio blob")
    blob = await s.get(blobs.Blob, track.blob_id)
    if blob is None:
        raise ValueError("audio blob is missing")
    album = await s.get(Album, track.album_id) if track.album_id else None
    source = await blobs.audio_path(blob)
    name = Path(track.path or f"{track.uid}.mp3").name
    dest_dir = get_settings().staging_dir / "export" / uuid.uuid4().hex
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    await asyncio.to_thread(shutil.copyfile, source, dest)
    cover = None
    if album is not None:
        found = await covers.cover_bytes(s, album)
        cover = found[1] if found is not None else None
    if album is not None:
        data = tagdata_for(track, album, cover=cover)
        await asyncio.to_thread(tagger.write_tags, dest, data)
    return dest


def cleanup(path: Path) -> None:
    try:
        shutil.rmtree(path.parent, ignore_errors=True)
    except OSError:  # nothing to clean up
        return


__all__ = ["cleanup", "materialize"]
