"""Import an audio file into the library: album row, immutable blob, Track row (PLAN 8.4).

The original bytes are hashed and uploaded to the object store unchanged: no tags are written on import, so the
same file imported twice yields one blob shared by both tracks. The staging file stays where the caller put it.
The catalog row is the source of truth: a download is finalized by `catalog_commit`;
separate coalesced, best-effort `nd_sync` until stage 5/7.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import cast

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.library import tagger
from app.library.artists import link_album_artist
from app.library.layout import album_dirname, sanitize_component, track_filename, version_suffixed
from app.library.tagger import ReleaseType
from app.matching.normalize import fingerprint, normalize_artist, normalize_title
from app.models import Album, Track, utcnow
from app.storage import blobs

log = logging.getLogger(__name__)

# one import / library edit at a time: they touch the same folders and album rows
library_lock = asyncio.Lock()


class ImportSpec(BaseModel):
    artist: str
    title: str
    artists: list[str] = []
    album: str | None = None
    album_artist: str | None = None
    album_id: int | None = None  # existing Hub album (uploads, editor)
    year: int | None = None
    release_date: date | None = None
    release_type: str | None = None
    track_no: int | None = None
    track_total: int | None = None
    disc_no: int | None = None
    genre: str | None = None
    isrc: str | None = None
    explicit: bool | None = None
    deezer_track_id: str | None = None
    deezer_album_id: str | None = None
    upc: str | None = None
    cover: bytes | None = None
    source: str | None = None
    source_url: str | None = None
    source_upload_date: date | None = None
    duration_s: int | None = None
    bitrate_kbps: int | None = None
    codec: str | None = None
    warnings: list[str] = []
    added_by: int | None = None
    force: bool = False  # explicit re-download: keep the old file, add a "[vN]" version next to it


def album_reldir(album: Album) -> PurePosixPath:
    return PurePosixPath(sanitize_component(album.album_artist), album_dirname(album.year, album.title))


def library_abs(rel: str | PurePosixPath) -> Path:
    return get_settings().library_dir / Path(*PurePosixPath(rel).parts)


async def find_album(s: AsyncSession, *, title: str, album_artist: str, deezer_album_id: str | None) -> Album | None:
    if deezer_album_id:
        a = (
            await s.execute(select(Album).where(Album.deezer_album_id == str(deezer_album_id)).limit(1))
        ).scalar_one_or_none()
        if a:
            return a
    nt, na = normalize_title(title).text, normalize_artist(album_artist).text
    rows = (await s.execute(select(Album).where(Album.title == title))).scalars().all()
    if not rows:
        # same album typed with a different case/ё/punctuation
        rows = (await s.execute(select(Album))).scalars().all()
    for a in rows:
        if normalize_title(a.title).text == nt and normalize_artist(a.album_artist).text == na:
            if deezer_album_id and a.deezer_album_id and a.deezer_album_id != str(deezer_album_id):
                continue  # same name, different release (e.g. deluxe)
            return a
    return None


async def get_or_create_album(s: AsyncSession, spec: ImportSpec) -> Album:
    if spec.album_id:
        a = await s.get(Album, spec.album_id)
        if a is None:
            raise ValueError(f"album {spec.album_id} not found")
        return a
    title = spec.album or spec.title
    album_artist = spec.album_artist or spec.artist
    a = await find_album(s, title=title, album_artist=album_artist, deezer_album_id=spec.deezer_album_id)
    if a is None:
        a = Album(
            uid=str(uuid.uuid4()),
            title=title,
            album_artist=album_artist,
            year=spec.year or (spec.release_date.year if spec.release_date else None),
            release_type=spec.release_type or ("single" if not spec.album else "album"),
            deezer_album_id=spec.deezer_album_id,
            upc=spec.upc,
            genre=spec.genre,
            created_by=spec.added_by,
        )
        s.add(a)
        await s.flush()
    elif spec.deezer_album_id and not a.deezer_album_id:
        a.deezer_album_id = spec.deezer_album_id
    # lazy artist: the album's credit names the entity, created on first use and reused afterwards
    await link_album_artist(s, a)
    return a


def tagdata_for(
    track: Track,
    album: Album,
    *,
    cover: bytes | None = None,
    artists: list[str] | None = None,
    track_total: int | None = None,
) -> tagger.TagData:
    return tagger.TagData(
        title=track.title,
        artist=track.artist,
        artists=artists or [],
        album=album.title,
        album_artist=album.album_artist,
        track_no=track.track_no,
        track_total=track_total,
        disc_no=track.disc_no,
        date=str(album.year) if album.year else None,
        genre=track.genre or album.genre,
        isrc=track.isrc,
        explicit=track.explicit,
        release_type=cast(ReleaseType, album.release_type),  # written only from catalog literals
        track_uid=track.uid,
        album_uid=album.uid,
        source=track.source,
        source_url=track.source_url,
        source_date=track.source_upload_date,
        acquired=track.acquired_at,
        deezer_track_id=track.deezer_track_id,
        deezer_album_id=album.deezer_album_id,
        cover=cover,
    )


def is_original(source_date: date | None, explicit: bool | None) -> bool:
    """Pre-censorship upload or explicit version: never replaced automatically (PLAN 8.6)."""
    cutoff = get_settings().cfg.matching.original_before
    return bool(explicit) or (source_date is not None and source_date < cutoff)


def _cover_relpath(album: Album) -> str:
    return (album_reldir(album) / "cover.jpg").as_posix()


def manifest_payload(track: Track, album: Album, sha256: str) -> dict[str, object]:
    """What `manifests/<sha256>/<uid>.json` keeps: enough to rebuild the track's metadata without the DB."""
    return {
        "sha256": sha256,
        "uid": track.uid,
        "album_uid": album.uid,
        "artist": track.artist,
        "title": track.title,
        "album": track.album,
        "album_artist": track.album_artist,
        "track_no": track.track_no,
        "disc_no": track.disc_no,
        "year": track.year,
        "genre": track.genre,
        "isrc": track.isrc,
        "duration_s": track.duration_s,
        "source": track.source,
        "source_url": track.source_url,
        "deezer_track_id": track.deezer_track_id,
        "deezer_album_id": album.deezer_album_id,
    }


async def logical_path_free(s: AsyncSession, rel: PurePosixPath) -> bool:
    return (await s.execute(select(Track.id).where(Track.path == rel.as_posix()).limit(1))).scalar_one_or_none() is None


async def unique_logical_path(s: AsyncSession, rel: PurePosixPath, *, force: bool = False) -> PurePosixPath:
    """A logical path unique within the library; `force` puts a re-download next to the old one as `[vN]`."""
    if await logical_path_free(s, rel):
        return rel
    stem, ext = rel.stem, rel.suffix
    n = 2
    while True:
        candidate = rel.with_name(version_suffixed(rel.name, n) if force else f"{stem} ({n}){ext}")
        if await logical_path_free(s, candidate):
            return candidate
        n += 1


async def import_file(s: AsyncSession, src: Path, spec: ImportSpec) -> Track:
    """Hash `src` (a staging file), upload it as a blob and create the Track row (caller commits)."""
    async with library_lock:
        album = await get_or_create_album(s, spec)
        cover = await asyncio.to_thread(tagger.prepare_cover, spec.cover) if spec.cover else None
        sha256, size = await asyncio.to_thread(blobs.sha256_file, src)

        ext = src.suffix.lstrip(".").lower() or "mp3"
        name = track_filename(spec.disc_no, spec.track_no, spec.title, ext)
        rel = await unique_logical_path(s, PurePosixPath(album_reldir(album), name), force=spec.force)

        track = Track(
            uid=str(uuid.uuid4()),
            album_id=album.id,
            path=rel.as_posix(),
            artist=spec.artist,
            title=spec.title,
            album=album.title,
            album_artist=album.album_artist,
            track_no=spec.track_no,
            disc_no=spec.disc_no,
            year=album.year,
            genre=spec.genre or album.genre,
            duration_s=spec.duration_s,
            bitrate_kbps=spec.bitrate_kbps,
            codec=spec.codec,
            isrc=spec.isrc.upper() if spec.isrc else None,
            deezer_track_id=str(spec.deezer_track_id) if spec.deezer_track_id else None,
            explicit=spec.explicit,
            source=spec.source,
            source_url=spec.source_url,
            source_upload_date=spec.source_upload_date,
            acquired_at=utcnow(),
            locked=is_original(spec.source_upload_date, spec.explicit),
            fingerprint=fingerprint(spec.artist, spec.title),
            warnings=list(spec.warnings),
            added_by=spec.added_by,
        )

        metadata = {
            "sha256": sha256,
            "uid": track.uid,
            "artist": spec.artist,
            "title": spec.title,
            "album": album.title,
        }
        blob = await blobs.store_audio(s, src, metadata=metadata, sha256=sha256, size=size)
        track.blob_id = blob.id

        if cover is not None:
            cover_blob = await blobs.store_cover(s, cover)
            if album.cover_blob_id is None:
                album.cover_blob_id = cover_blob.id
                album.cover_path = _cover_relpath(album)

        s.add(track)
        await s.flush()
        await blobs.write_manifest(sha256, track.uid, manifest_payload(track, album, sha256))
        log.info("imported %s as blob %s", track.path, sha256[:12], extra={"user_id": spec.added_by})
        return track


def staging_dir_for(kind: str, key: str | int) -> Path:
    d = get_settings().staging_dir / kind / str(key)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"cannot create staging directory {d}: {e}") from e
    return d


def cleanup_dir(d: Path) -> None:
    shutil.rmtree(d, ignore_errors=True)


def now_local_date() -> date:
    return datetime.now().date()
