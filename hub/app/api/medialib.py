"""The Navidrome library as the user's player sees it: artists, albums, «Понравившиеся», covers. The Android app
has these as native screens (Tempus); the PWA in a plain browser shows them from here (`web/src/pages/Home.tsx`,
`MediaLibrary.tsx`). Everything is asked of Navidrome as the user, so their stars and play counts are the ones shown.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import NdAlbumOut, NdArtistOut, NdArtistPageOut, NdHomeOut, NdLikedOut, NdSongOut
from app.auth import current_user, has_navidrome, user_navidrome
from app.db import get_session
from app.library.navidrome import NavidromeClient, SubsonicError
from app.mixes.resolve import library_track
from app.models import Album, Artist, CatalogCache, ImportItem, Track, User

router = APIRouter(prefix="/api/nd", tags=["medialib"])

ALBUM_LISTS = ("recent", "frequent", "newest", "starred", "alphabeticalByName", "alphabeticalByArtist", "random")
HOME_ROW = 15
HOME_RADIO = 30
SMALL_COVER = 300
BIG_COVER = 1000


def cover_url(cover_id: str | None, size: int = SMALL_COVER) -> str | None:
    return f"/api/nd/cover/{quote(cover_id, safe='')}?size={size}" if cover_id else None


def album_out(a: dict) -> NdAlbumOut:
    return NdAlbumOut(
        id=a["id"],
        title=a.get("name") or a.get("title") or "",
        artist=a.get("artist") or "",
        artist_id=a.get("artistId"),
        year=a.get("year") or None,
        song_count=a.get("songCount") or 0,
        duration_s=a.get("duration") or 0,
        cover_url=cover_url(a.get("coverArt")),
        starred=bool(a.get("starred")),
    )


def artist_out(a: dict) -> NdArtistOut:
    return NdArtistOut(
        id=a["id"],
        name=a.get("name") or "",
        album_count=a.get("albumCount") or 0,
        cover_url=cover_url(a.get("coverArt")),
        starred=bool(a.get("starred")),
    )


async def songs_out(s: AsyncSession, songs: list[dict]) -> list[NdSongOut]:
    """Navidrome songs as tracks: the Hub's own record of the file gives the Deezer ref (mixes, «Моя волна» and
    listening data know the song by it), the rest are `nd:<song>`."""
    ids = [x["id"] for x in songs if x.get("id")]
    rows: dict[str, Track] = {}
    for i in range(0, len(ids), 500):
        q = select(Track).where(Track.nd_song_id.in_(ids[i : i + 500]), Track.deleted_at.is_(None))
        rows.update((cast(str, t.nd_song_id), t) for t in (await s.execute(q)).scalars())
    out = []
    for x in songs:
        sid = x.get("id")
        if not sid:
            continue
        t = rows.get(sid)
        ref = library_track(t).ref if t is not None else f"nd:{sid}"
        artist = x.get("artist") or ""
        names = [a["name"] for a in x.get("artists") or [] if a.get("name")] or ([artist] if artist else [])
        out.append(
            NdSongOut(
                ref=ref,
                source="dz" if ref.startswith("dz:") else "nd",
                id=ref.split(":", 1)[1],
                title=x.get("title") or "",
                artist=artist,
                artists=names,
                album_title=x.get("album"),
                album_artist=x.get("displayAlbumArtist"),
                duration_s=x.get("duration"),
                track_no=x.get("track"),
                disc_no=x.get("discNumber"),
                isrc=(x.get("isrc") or [None])[0] if isinstance(x.get("isrc"), list) else x.get("isrc"),
                explicit=t.explicit if t is not None else None,
                cover_url=cover_url(x.get("coverArt"), BIG_COVER),
                cover_small_url=cover_url(x.get("coverArt")),
                owned=True,
                library_track_id=t.id if t is not None else None,
                nd_song_id=sid,
                nd_album_id=x.get("albumId"),
                nd_artist_id=x.get("artistId"),
            )
        )
    return out


async def nd_call(user: User, fn, not_found: str = "Не найдено в библиотеке"):
    """Runs `fn(nd)` as the user and turns Navidrome failures into HTTP errors: «not found» (70) → 404,
    «not authorized» (50, e.g. not the playlist's owner) → 403, any other Subsonic error or no answer → 502."""
    nd = user_navidrome(user)
    try:
        return await fn(nd)
    except SubsonicError as e:
        if e.code == 70:  # not found
            raise HTTPException(404, not_found) from e
        if e.code == 50:  # not authorized
            raise HTTPException(403, "Нет прав на это в библиотеке") from e
        raise HTTPException(502, f"Navidrome: {e.message}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, "Navidrome не отвечает") from e
    finally:
        await nd.aclose()


def _radio(artists: list[dict]) -> list[NdArtistOut]:
    """«Радиостанции»: every artist, the liked ones first, then those with more albums."""
    ranked = sorted(
        artists, key=lambda a: (not a.get("starred"), -(a.get("albumCount") or 0), (a.get("name") or "").casefold())
    )
    return [artist_out(a) for a in ranked]


# --- the stage-4 transition: no Navidrome account, the Hub catalog answers in ND's shape ---------------


def hub_album_out(album: Album, artist: Artist | None, song_count: int = 0, duration_s: int = 0) -> NdAlbumOut:
    """A Hub album as the ND library screen expects it (the ids are the catalog `uid`s)."""
    return NdAlbumOut(
        id=album.uid,
        title=album.title,
        artist=artist.name if artist is not None else album.album_artist,
        artist_id=artist.uid if artist is not None else None,
        year=album.year,
        song_count=song_count,
        duration_s=duration_s,
        cover_url=None,
        starred=False,
    )


def hub_artist_out(artist: Artist, album_count: int = 0) -> NdArtistOut:
    return NdArtistOut(id=artist.uid, name=artist.name, album_count=album_count, cover_url=None, starred=False)


def hub_song_out(track: Track) -> NdSongOut:
    """A Hub catalog track in the ND song shape: the library screens of the browser PWA keep working."""
    ref = library_track(track).ref
    source, _, ident = ref.partition(":")
    return NdSongOut(
        ref=ref,
        source=source if source in ("dz", "hm", "mb", "nd", "file") else "hm",  # type: ignore[arg-type]
        id=ident or str(track.id),
        title=track.title,
        artist=track.artist,
        artists=[track.artist],
        album_title=track.album,
        album_artist=track.album_artist,
        duration_s=track.duration_s,
        track_no=track.track_no,
        disc_no=track.disc_no,
        explicit=track.explicit,
        owned=True,
        library_track_id=track.id,
    )


async def hub_album_rows(s: AsyncSession, type: str, offset: int = 0, size: int = 50) -> list[NdAlbumOut]:
    """Albums of the Hub catalog for the ND list `type`s (`starred` is empty: ♥ move to the Hub in stage 5)."""
    if type == "starred":
        return []
    stats = (
        select(
            Track.album_id,
            func.count(Track.id).label("songs"),
            func.coalesce(func.sum(Track.duration_s), 0).label("duration"),
        )
        .where(Track.deleted_at.is_(None), Track.album_id.is_not(None))
        .group_by(Track.album_id)
        .subquery()
    )
    order = {
        "alphabeticalByArtist": (Album.album_artist, Album.title),
        "newest": (Album.created_at.desc(),),
        "recent": (Album.updated_at.desc(),),
        "frequent": (Album.updated_at.desc(), Album.title),
        "random": (func.random(),),
    }.get(type, (Album.title,))
    rows = await s.execute(
        select(Album, Artist, stats.c.songs, stats.c.duration)
        .outerjoin(Artist, Artist.id == Album.artist_id)
        .outerjoin(stats, stats.c.album_id == Album.id)
        .order_by(*order)
        .offset(offset)
        .limit(size)
    )
    return [hub_album_out(a, ar, songs or 0, duration or 0) for a, ar, songs, duration in rows.unique().all()]


async def hub_home(s: AsyncSession) -> NdHomeOut:
    """The Home screen of a user without a Navidrome account: Hub albums and artists, an empty ♥ row."""
    recent = await hub_album_rows(s, "recent", size=HOME_ROW)
    newest = await hub_album_rows(s, "newest", size=HOME_ROW)
    rows = await s.execute(
        select(Artist, func.count(Album.id))
        .outerjoin(Album, Album.artist_id == Artist.id)
        .group_by(Artist.id)
        .order_by(func.count(Album.id).desc(), Artist.name)
        .limit(HOME_RADIO)
    )
    return NdHomeOut(
        recent=recent,
        frequent=newest,
        newest=newest,
        starred=[],
        radio=[hub_artist_out(a, n) for a, n in rows.all()],
    )


async def hub_album_detail(s: AsyncSession, album_id: str) -> NdAlbumOut:
    album = await hub_album(s, album_id)
    if album is None:
        raise HTTPException(404, "Альбом не найден")
    artist = await s.get(Artist, album.artist_id) if album.artist_id else None
    tracks = list(
        (
            await s.execute(
                select(Track).where(Track.album_id == album.id, Track.deleted_at.is_(None)).order_by(Track.track_no)
            )
        ).scalars()
    )
    out = hub_album_out(album, artist, len(tracks), sum(t.duration_s or 0 for t in tracks))
    out.tracks = [hub_song_out(t) for t in tracks]
    return out


async def hub_album(s: AsyncSession, album_id: str) -> Album | None:
    """A Hub album by its catalog `uid` (or the legacy int id the old PWA kept)."""
    if album_id.isdigit():
        legacy = await s.get(Album, int(album_id))
        if legacy is not None:
            return legacy
    return (await s.execute(select(Album).where(Album.uid == album_id))).scalar_one_or_none()


async def hub_artist(s: AsyncSession, artist_id: str) -> Artist | None:
    if artist_id.isdigit():
        legacy = await s.get(Artist, int(artist_id))
        if legacy is not None:
            return legacy
    return (await s.execute(select(Artist).where(Artist.uid == artist_id))).scalar_one_or_none()


async def hub_artist_page(s: AsyncSession, artist_id: str) -> NdArtistPageOut:
    artist = await hub_artist(s, artist_id)
    if artist is None:
        raise HTTPException(404, "Исполнитель не найден")
    albums = list(
        (
            await s.execute(
                select(Album).where(Album.artist_id == artist.id).order_by(Album.year.desc().nulls_last(), Album.title)
            )
        ).scalars()
    )
    counts = dict(
        (
            await s.execute(
                select(Track.album_id, func.count(Track.id))
                .where(Track.album_id.in_([a.id for a in albums]), Track.deleted_at.is_(None))
                .group_by(Track.album_id)
            )
        ).all()
    )
    return NdArtistPageOut(
        artist=hub_artist_out(artist, len(albums)),
        albums=[hub_album_out(a, artist, counts.get(a.id, 0)) for a in albums],
    )


@router.get("/home", response_model=NdHomeOut)
async def home(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    if not has_navidrome(user):
        return await hub_home(s)

    async def load(nd: NavidromeClient):
        return await asyncio.gather(
            *(nd.get_album_list2(t, size=HOME_ROW) for t in ("recent", "frequent", "newest", "starred")),
            nd.get_artists(),
        )

    recent, frequent, newest, starred, artists = await nd_call(user, load)
    return NdHomeOut(
        recent=[album_out(a) for a in recent],
        frequent=[album_out(a) for a in frequent],
        newest=[album_out(a) for a in newest],
        starred=[album_out(a) for a in starred],
        radio=_radio(artists)[:HOME_RADIO],
    )


@router.get("/albums", response_model=list[NdAlbumOut])
async def albums(
    type: str = "alphabeticalByName",
    offset: int = Query(0, ge=0),
    size: int = Query(50, ge=1, le=500),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    if type not in ALBUM_LISTS:
        raise HTTPException(422, f"type: one of {', '.join(ALBUM_LISTS)}")
    if not has_navidrome(user):
        return await hub_album_rows(s, type, offset, size)
    return [album_out(a) for a in await nd_call(user, lambda nd: nd.get_album_list2(type, size=size, offset=offset))]


@router.get("/albums/{album_id}", response_model=NdAlbumOut)
async def album(
    album_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    if not has_navidrome(user):
        return await hub_album_detail(s, album_id)
    a = await nd_call(user, lambda nd: nd.get_album(album_id))
    if not a.get("id"):
        raise HTTPException(404, "Альбом не найден")
    out = album_out(a)
    out.tracks = await songs_out(s, a.get("song") or [])
    return out


@router.get("/artists", response_model=list[NdArtistOut])
async def artists(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    if not has_navidrome(user):
        rows = await s.execute(
            select(Artist, func.count(Album.id)).outerjoin(Album, Album.artist_id == Artist.id).group_by(Artist.id)
        )
        return [hub_artist_out(a, n) for a, n in rows.all()]
    return [artist_out(a) for a in await nd_call(user, lambda nd: nd.get_artists())]


@router.get("/artists/{artist_id}", response_model=NdArtistPageOut)
async def artist(
    artist_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    if not has_navidrome(user):
        return await hub_artist_page(s, artist_id)
    a = await nd_call(user, lambda nd: nd.get_artist(artist_id))
    if not a.get("id"):
        raise HTTPException(404, "Исполнитель не найден")
    # newest year first, albums without a year last, ties by title
    albums_ = sorted(
        a.get("album") or [],
        key=lambda x: (
            not x.get("year"),
            -(x.get("year") or 0),
            (x.get("name") or x.get("title") or "").casefold(),
            x.get("id") or "",
        ),
    )
    return NdArtistPageOut(artist=artist_out(a), albums=[album_out(x) for x in albums_])


@router.get("/liked", response_model=NdLikedOut)
async def liked(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    from app.favorites import parse_time, snapshot
    from app.imports.apply import _user_likes

    if not has_navidrome(user):
        # ♥ of the Hub (`favorite_subjects`) arrive with stage 5: until then the row is empty, not an error
        return NdLikedOut()

    body = await nd_call(user, lambda nd: nd.get_starred2())
    states = await snapshot(s, user.id)
    decisions = {(st.target_type, alias): st for st in states.values() for alias in st.aliases}

    def state(typ, *, ref=None, nd_id=None):
        return decisions.get((typ, f"ref:{ref}")) or decisions.get((typ, f"nd:{nd_id}"))

    # The same own decisions as /api/starred override delayed ND/import snapshots.
    ranked = []
    seen = set()
    songs = body.get("song") or []
    for obj, song in zip([x for x in songs if x.get("id")], await songs_out(s, songs), strict=True):
        st = state("track", ref=song.ref, nd_id=song.nd_song_id)
        if st is not None and not st.liked:
            continue
        seen.update([f"ref:{song.ref}", f"nd:{song.nd_song_id}", *(st.aliases if st else [])])
        ranked.append((st.since if st else parse_time(obj.get("starred")), song))

    pending = {}
    for item in (await s.execute(_user_likes(user.id).where(ImportItem.status.in_(("waiting", "queued"))))).scalars():
        st = state("track", ref=item.ref)
        if st is None:
            pending.setdefault(item.ref, (item.raw_json or {}, None, None, []))
    for st in states.values():
        if st.target_type == "track" and st.liked:
            ref = st.ref or (f"nd:{st.nd_id}" if st.nd_id else None)
            if ref:
                pending[ref] = (st.data or {}, st.since, st.nd_id, st.aliases)

    # Read stored metadata in batches. Listing likes must never depend on a catalog
    # request or start a download; old likes can recover names from playback caches.
    cache_keys = {
        key
        for ref in pending
        for key in (f"listen:{ref}", f"mix:track:{ref}", f"dz:track:{ref[3:]}" if ref.startswith("dz:") else "")
        if key
    }
    cached = {}
    keys = sorted(cache_keys)
    for start in range(0, len(keys), 500):
        for row in (
            await s.execute(select(CatalogCache).where(CatalogCache.key.in_(keys[start : start + 500])))
        ).scalars():
            cached[row.key] = row.payload_json.get("v") or {}
    for ref, (data, since, nd_id, links) in pending.items():
        aliases = {f"ref:{ref}", *links, *([f"nd:{nd_id}"] if nd_id else [])}
        if seen.intersection(aliases):
            continue
        meta = {
            **cached.get(f"listen:{ref}", {}),
            **cached.get(f"mix:track:{ref}", {}),
            **cached.get(f"dz:track:{ref[3:]}", {}),
            **data,
        }
        src, _, ident = ref.partition(":")
        if src not in ("dz", "hm", "mb", "nd", "file") or not ident:
            continue
        song = NdSongOut(
            ref=ref,
            source=src,
            id=ident,
            title=meta.get("title") or ref,
            artist=meta.get("artist") or "",
            duration_s=meta.get("duration_s") or meta.get("duration"),
            album_title=meta.get("album_title"),
            artist_ref=meta.get("artist_ref"),
            album_ref=meta.get("album_ref"),
            cover_url=meta.get("cover_url"),
            cover_small_url=meta.get("cover_small_url"),
            nd_song_id=nd_id,
            owned=bool(nd_id),
        )
        ranked.append((since or parse_time(meta.get("liked_at")), song))
        seen.update(aliases)

    def visible(typ, obj):
        st = state(typ, nd_id=obj.get("id"))
        return st is None or st.liked

    ranked.sort(key=lambda pair: pair[0] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return NdLikedOut(
        songs=[song for _, song in ranked],
        albums=[
            album_out(a)
            for a in sorted(body.get("album") or [], key=lambda x: x.get("starred") or "", reverse=True)
            if visible("album", a)
        ],
        artists=[artist_out(a) for a in body.get("artist") or [] if visible("artist", a)],
    )


@router.get("/cover/{cover_id}")
async def cover(cover_id: str, size: int | None = Query(None, ge=32, le=1500), user: User = Depends(current_user)):
    """A Navidrome cover (getCoverArt), so the page needs no Navidrome credentials."""
    if not has_navidrome(user):
        raise HTTPException(404, "Нет обложки")
    nd = user_navidrome(user)
    try:
        url, params = nd.cover_art_url_params(cover_id, size)
        r = await nd.http.get(url, params=params)
    except httpx.HTTPError as e:
        raise HTTPException(502, "Navidrome не отвечает") from e
    finally:
        await nd.aclose()
    kind = r.headers.get("content-type", "")
    if r.status_code != 200 or not kind.startswith("image/"):
        raise HTTPException(404, "Нет обложки")
    return Response(r.content, media_type=kind, headers={"Cache-Control": "private, max-age=86400"})
