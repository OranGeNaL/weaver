"""Lazy artists: one row per normalized name, created by an upsert at import/edit time.

`name_norm = normalize_artist(name).latin or .text` is the unique key. UNIQUE deliberately merges namesakes: telling
two artists with one spelling apart is a separate manual operation, not a guess of the importer. `tracks.artist` and
`albums.album_artist` stay as credits for display and export.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.matching.normalize import normalize_artist
from app.models import Album, Artist, Track, utcnow


def artist_name_norm(name: str) -> str:
    """The deduplication key of an artist name (empty for a name with no letters/digits)."""
    norm = normalize_artist(name)
    return norm.latin or norm.text


async def ensure_artist(session: AsyncSession, name: str) -> Artist:
    """The artist of `name`, created on first use; safe for parallel imports of the same name."""
    name = " ".join((name or "").split())
    norm = artist_name_norm(name)
    if not norm:
        raise ValueError("пустое имя артиста")
    row = {
        "uid": str(uuid.uuid4()),
        "name": name,
        "name_norm": norm,
        "sort_name": normalize_artist(name).text or name,
        "created_at": utcnow(),
    }
    stmt = insert(Artist).values(**row)
    # DO UPDATE (not DO NOTHING) makes the conflicting insert wait for and return the winner's row
    stmt = stmt.on_conflict_do_update(index_elements=[Artist.name_norm], set_={"name": stmt.excluded.name})
    artist_id = (await session.execute(stmt.returning(Artist.id))).scalar_one()
    artist = await session.get(Artist, artist_id)
    if artist is None:
        raise RuntimeError("artist upsert returned no row")
    return artist


async def link_album_artist(s: AsyncSession, album: Album, *, force: bool = False) -> Artist | None:
    """Point an album at the lazy artist of its `album_artist` credit (no-op if it already points at one)."""
    if album.artist_id is not None and not force:
        return await s.get(Artist, album.artist_id)
    if not artist_name_norm(album.album_artist):
        return None
    artist = await ensure_artist(s, album.album_artist)
    album.artist_id = artist.id
    await s.flush()
    return artist


async def merge_artists(s: AsyncSession, source: Artist, target: Artist) -> Artist:
    """Move albums (and the matching track credits) from `source` to `target`; delete the source row."""
    if source.id == target.id:
        return target
    albums = (await s.execute(select(Album).where(Album.artist_id == source.id))).scalars().all()
    for album in albums:
        album.artist_id = target.id
        album.album_artist = target.name
    # tracks are linked to an artist through their album; the credit line is updated where it names the source
    for track in (await s.execute(select(Track).where(Track.album_id.in_([a.id for a in albums])))).scalars():
        if track.artist and artist_name_norm(track.artist) == source.name_norm:
            track.artist = target.name
    await s.flush()
    await s.delete(source)
    await s.flush()
    return target


__all__ = ["artist_name_norm", "ensure_artist", "link_album_artist", "merge_artists"]
