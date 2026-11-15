"""Hub playlists: the PWA contract of `/api/playlists` since stage 5 (the Hub owns playlists)."""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    NdSongOut,
    PlaylistAddIn,
    PlaylistCreateIn,
    PlaylistOut,
    PlaylistPageOut,
    PlaylistRemoveIn,
    PlaylistReorderIn,
)
from app.auth import current_user
from app.db import get_session
from app.models import Album, Playlist, Track, User
from app.playlists import add_tracks, create, get_owned, list_for_user, remove, remove_indexes, set_order, tracks

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def pl_out(p: Playlist, song_count: int = 0, duration_s: int = 0) -> PlaylistOut:
    return PlaylistOut(id=p.uid, name=p.name, song_count=song_count, duration_s=duration_s)


def track_ref(t: Track) -> str:
    """A library track as a ref: its Deezer record, else its deprecated Navidrome song, else its Hub uid."""
    if t.deezer_track_id:
        return f"dz:{t.deezer_track_id}"
    if t.nd_song_id:
        return f"nd:{t.nd_song_id}"
    return f"lib:{t.uid}"


def ref_source(ref: str) -> Literal["dz", "hm", "mb", "nd", "file"]:
    prefix = ref.split(":", 1)[0]
    return cast(Literal["dz", "hm", "mb", "nd", "file"], prefix)


async def songs_of(s: AsyncSession, tracks_: list[Track]) -> list[NdSongOut]:
    """The playlist's tracks in order, as the PWA's player expects them."""
    album_ids = {t.album_id for t in tracks_ if t.album_id}
    albums = (
        {a.id: a for a in (await s.execute(select(Album).where(Album.id.in_(album_ids)))).scalars()}
        if album_ids
        else {}
    )
    out = []
    for t in tracks_:
        ref = track_ref(t)
        album = albums.get(t.album_id) if t.album_id else None
        cover = f"/api/library/albums/{album.uid}/cover" if album is not None and album.cover_blob_id else None
        out.append(
            NdSongOut(
                ref=ref,
                source=ref_source(ref),
                id=ref.split(":", 1)[1],
                title=t.title,
                artist=t.artist,
                artists=[t.artist] if t.artist else [],
                album_title=t.album,
                album_artist=t.album_artist,
                duration_s=t.duration_s,
                track_no=t.track_no,
                disc_no=t.disc_no,
                isrc=t.isrc,
                explicit=t.explicit,
                cover_url=cover,
                cover_small_url=cover,
                owned=True,
                library_track_id=t.id,
                uid=t.uid,
                nd_song_id=t.nd_song_id,
                nd_album_id=None,
                nd_artist_id=None,
            )
        )
    return out


@router.get("", response_model=list[PlaylistOut])
async def list_playlists(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return [pl_out(p, count, duration) for p, count, duration in await list_for_user(s, user.id)]


@router.post("", response_model=PlaylistOut)
async def create_playlist(
    body: PlaylistCreateIn,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    if not body.name.strip():
        raise HTTPException(400, "Введите название")
    return pl_out(await create(s, user.id, body.name))


async def _owned(s: AsyncSession, user: User, uid: str) -> Playlist:
    playlist = await get_owned(s, user.id, uid)
    if playlist is None:
        raise HTTPException(404, "Плейлист не найден")
    return playlist


async def _resolve_uids(s: AsyncSession, body: PlaylistAddIn) -> list[str]:
    """The Hub track uids the request names: `uids` first, then `library_track_ids`, then deprecated `song_ids`."""
    uids = list(dict.fromkeys(body.uids))
    if body.library_track_ids:
        by_id = {
            t.id: t.uid
            for t in (
                await s.execute(select(Track).where(Track.id.in_(body.library_track_ids), Track.deleted_at.is_(None)))
            ).scalars()
        }
        uids += [by_id[i] for i in body.library_track_ids if i in by_id]
    if body.song_ids:
        found = {
            t.nd_song_id: t.uid
            for t in (
                await s.execute(select(Track).where(Track.nd_song_id.in_(body.song_ids), Track.deleted_at.is_(None)))
            ).scalars()
        }
        uids += [found[x] for x in body.song_ids if x in found]
    return list(dict.fromkeys(uids))


@router.post("/{playlist_id}/add")
async def add(
    playlist_id: str,
    body: PlaylistAddIn,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    playlist = await _owned(s, user, playlist_id)
    uids = await _resolve_uids(s, body)
    if not uids:
        raise HTTPException(400, "Эти треки ещё не появились в библиотеке")
    added = await add_tracks(s, playlist, uids, anchor_uid=body.anchor_uid, added_by=user.id)
    return {"ok": True, "added": len(added), "uids": [i.uid for i in added]}


@router.post("/{playlist_id}/reorder")
async def reorder(
    playlist_id: str,
    body: PlaylistReorderIn,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    playlist = await _owned(s, user, playlist_id)
    await set_order(s, playlist, body.item_uids)
    return {"ok": True}


@router.get("/{playlist_id}", response_model=PlaylistPageOut)
async def get_playlist(
    playlist_id: str,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    playlist = await _owned(s, user, playlist_id)
    found = await tracks(s, playlist.id)
    out = await songs_of(s, found)
    duration = sum(t.duration_s or 0 for t in out)
    # one entry per item and one item per index: `/remove` numbers exactly this list
    return PlaylistPageOut(**pl_out(playlist, len(found), int(duration)).model_dump(), tracks=out)


@router.post("/{playlist_id}/remove")
async def remove_items(
    playlist_id: str,
    body: PlaylistRemoveIn,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    """Takes songs out by their positions (a song may be in a playlist twice)."""
    playlist = await _owned(s, user, playlist_id)
    removed = await remove_indexes(s, playlist, body.indexes)
    return {"ok": True, "removed": removed}


@router.delete("/{playlist_id}")
async def delete(
    playlist_id: str,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    playlist = await _owned(s, user, playlist_id)
    await remove(s, playlist)
    return {"ok": True}
