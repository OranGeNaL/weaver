"""Library edits (FR-5): rename albums, move/merge tracks, covers, trash.

Audio bytes are immutable blobs: editing metadata, moving a track between albums or deleting it touches only the
DB and the logical path. Tags are written only on export/materialization (`app.library.export`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.library import tagger
from app.library.artists import link_album_artist
from app.library.importer import album_reldir, library_lock, unique_logical_path
from app.library.layout import track_filename
from app.matching.normalize import fingerprint
from app.models import Album, Track
from app.storage import blobs

log = logging.getLogger(__name__)

_COVER_NAME = "cover.jpg"


def _cover_relpath(album: Album) -> str:
    return (album_reldir(album) / _COVER_NAME).as_posix()


def _new_relpath(track: Track, album: Album) -> PurePosixPath:
    ext = PurePosixPath(track.path).suffix.lstrip(".") if track.path else "mp3"
    name = track_filename(track.disc_no, track.track_no, track.title, ext or "mp3")
    return PurePosixPath(album_reldir(album), name)


async def _relocate(s: AsyncSession, track: Track, album: Album, *, touching_album: bool) -> None:
    track.album_id = album.id
    track.album = album.title
    track.album_artist = album.album_artist
    track.year = album.year
    track.fingerprint = fingerprint(track.artist, track.title)
    if touching_album or not track.path:
        desired = _new_relpath(track, album)
        if desired.as_posix() != track.path:
            # uniquify only against *other* rows: a track never collides with its own current path
            track.path = (await unique_logical_path(s, desired)).as_posix()
            # the blob and the Navidrome link stay: only the logical path changes (stage 3: edits MUST NOT
            # require a rescan or a re-link)


async def relocate_track(s: AsyncSession, track: Track, album: Album, *, retag: bool = True) -> None:
    """Point the track at another album; only the logical path changes, the blob is untouched.

    `retag` is accepted for call compatibility: tags are not written on ordinary edits any more.
    """
    await _relocate(s, track, album, touching_album=True)


async def update_album(s: AsyncSession, album: Album, **fields) -> Album:
    async with library_lock:
        for k, v in fields.items():
            if v is not None:
                setattr(album, k, v)
        if "album_artist" in fields and fields["album_artist"] is not None:
            # a renamed credit is a new lazy artist (or the existing one under the new normalization)
            await link_album_artist(s, album, force=True)
        tracks = (await s.execute(select(Track).where(Track.album_id == album.id))).scalars().all()
        for t in tracks:
            await _relocate(s, t, album, touching_album=True)
        if album.cover_blob_id is not None:
            album.cover_path = _cover_relpath(album)
        await s.flush()
        return album


async def set_album_cover(s: AsyncSession, album: Album, image: bytes) -> Album:
    cover = await asyncio.to_thread(tagger.prepare_cover, image)
    async with library_lock:
        blob = await blobs.store_cover(s, cover)
        album.cover_blob_id = blob.id
        album.cover_path = _cover_relpath(album)
        await s.flush()
        return album


async def move_tracks(s: AsyncSession, track_ids: list[int], target: Album) -> list[Album]:
    """Move tracks into `target`; returns source albums that became empty (already deleted)."""
    async with library_lock:
        tracks = (await s.execute(select(Track).where(Track.id.in_(track_ids)))).scalars().all()
        sources = {t.album_id for t in tracks if t.album_id and t.album_id != target.id}
        for t in tracks:
            if t.album_id == target.id:
                continue
            await _relocate(s, t, target, touching_album=True)
        await s.flush()
        removed = []
        for aid in sources:
            n = (await s.execute(select(func.count(Track.id)).where(Track.album_id == aid))).scalar_one()
            if n == 0:
                a = await s.get(Album, aid)
                if a:
                    removed.append(a)
                    await s.delete(a)
        await s.flush()
        return removed


async def merge_albums(s: AsyncSession, source_ids: list[int], target: Album) -> Album:
    ids = await s.execute(select(Track.id).where(Track.album_id.in_([i for i in source_ids if i != target.id])))
    await move_tracks(s, list(ids.scalars()), target)
    return target


async def update_track(s: AsyncSession, track: Track, **fields) -> Track:
    async with library_lock:
        for k, v in fields.items():
            if v is not None:
                setattr(track, k, v)
        album = await s.get(Album, track.album_id) if track.album_id else None
        if album is not None:
            await _relocate(s, track, album, touching_album=False)
        await s.flush()
        return track


async def trash_track(s: AsyncSession, track: Track) -> None:
    """Mark the track deleted: the row and its blob survive for `library.trash_days` (restore is possible)."""
    track.deleted_at = datetime.now(UTC)
    await s.flush()


async def restore_track(s: AsyncSession, track: Track) -> None:
    track.deleted_at = None
    await s.flush()


async def purge_trash(days: int) -> int:
    """Delete track rows whose trash period is over; the blob GC frees objects without references afterwards."""
    from app.db import session_scope

    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with session_scope() as s:
        rows = list(
            (await s.execute(select(Track).where(Track.deleted_at.is_not(None), Track.deleted_at < cutoff))).scalars()
        )
        album_ids = {t.album_id for t in rows if t.album_id is not None}
        for t in rows:
            await s.delete(t)
        await s.flush()
        # an album whose last track left the trash has no reason to stay
        for album_id in album_ids:
            left = (await s.execute(select(func.count(Track.id)).where(Track.album_id == album_id))).scalar_one()
            if not left:
                album = await s.get(Album, album_id)
                if album is not None:
                    await s.delete(album)
    if rows:
        log.info("trash: deleted %s track rows older than %s days", len(rows), days)
    return len(rows)


def file_size(path: Path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
