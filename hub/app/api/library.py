"""Own library: albums, tracks, covers, stars (FR-4 «В коллекцию», FR-5 editor)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.api.schemas import (
    AlbumCreateIn,
    AlbumMergeIn,
    AlbumPatchIn,
    ArtistMergeIn,
    LibAlbumOut,
    LibArtistOut,
    LibArtistPageOut,
    LibHomeOut,
    LibLikedOut,
    LibraryImportIn,
    LibraryImportOut,
    LibTrackOut,
    StarIn,
    StarredOut,
    TrackPatchIn,
    TracksMoveIn,
    TrackSourceOut,
)
from app.auth import admin_user, current_user
from app.catalog.models import split_ref
from app.config import get_settings
from app.db import get_session
from app.events import bus
from app.jobs.queue import notify
from app.library import artists as artists_lib
from app.library import covers, editor, export, probe, tagger
from app.library.search import ilike_all
from app.models import Album, Artist, PlayEvent, Track, User

router = APIRouter(tags=["library"])
log = logging.getLogger(__name__)


LIB_ALBUM_SORTS = (
    "updated_at",
    "recent",
    "frequent",
    "newest",
    "starred",
    "alphabeticalByName",
    "alphabeticalByArtist",
    "random",
)
HOME_ROW = 15
HOME_ARTISTS = 30


def track_out(t: Track, *, album_uid: str | None = None, artist_uid: str | None = None) -> LibTrackOut:
    return LibTrackOut(
        uid=t.uid,
        album_uid=album_uid,
        artist_uid=artist_uid,
        id=t.id,
        album_id=t.album_id,
        integrity_issue=t.integrity_issue,
        artist=t.artist,
        title=t.title,
        album=t.album,
        album_artist=t.album_artist,
        track_no=t.track_no,
        disc_no=t.disc_no,
        year=t.year,
        duration_s=t.duration_s,
        bitrate_kbps=t.bitrate_kbps,
        codec=t.codec,
        explicit=t.explicit,
        source=t.source,
        locked=t.locked,
        warnings=t.warnings or [],
        path=t.path,
    )


def _has_cover(a: Album) -> bool:
    return a.cover_blob_id is not None


def _album_brief(a: Album, *, track_count: int = 0, artist_uid: str | None = None) -> LibAlbumOut:
    return LibAlbumOut(
        uid=a.uid,
        id=a.id,
        artist_uid=artist_uid,
        title=a.title,
        album_artist=a.album_artist,
        year=a.year,
        release_type=a.release_type,
        genre=a.genre,
        deezer_album_id=a.deezer_album_id,
        track_count=track_count,
        has_cover=_has_cover(a),
        integrity_issue=a.integrity_issue,
    )


async def _artist_uids(s: AsyncSession, albums: list[Album]) -> dict[int, str]:
    ids = {a.artist_id for a in albums if a.artist_id is not None}
    if not ids:
        return {}
    rows = (await s.execute(select(Artist.id, Artist.uid).where(Artist.id.in_(ids)))).all()
    return dict(rows)


async def _track_counts(s: AsyncSession, album_ids: list[int]) -> dict[int, int]:
    if not album_ids:
        return {}
    rows = (
        await s.execute(
            select(Track.album_id, func.count(Track.id))
            .where(Track.album_id.in_(album_ids), Track.deleted_at.is_(None))
            .group_by(Track.album_id)
        )
    ).all()
    return {int(album_id): count for album_id, count in rows if album_id is not None}


def _artist_uid(uids: dict[int, str], album: Album) -> str | None:
    return uids.get(album.artist_id) if album.artist_id is not None else None


async def _albums_out(s: AsyncSession, albums: list[Album], *, with_tracks: bool = False) -> list[LibAlbumOut]:
    uids = await _artist_uids(s, albums)
    counts = await _track_counts(s, [a.id for a in albums])
    out = []
    for a in albums:
        item = _album_brief(a, track_count=counts.get(a.id, 0), artist_uid=_artist_uid(uids, a))
        if with_tracks:
            item.tracks = await _album_tracks(s, a, _artist_uid(uids, a))
        out.append(item)
    return out


async def _album_tracks(s: AsyncSession, a: Album, artist_uid: str | None) -> list[LibTrackOut]:
    tracks = (
        (
            await s.execute(
                select(Track)
                .where(Track.album_id == a.id, Track.deleted_at.is_(None))
                .order_by(Track.disc_no, Track.track_no, Track.id)
            )
        )
        .scalars()
        .all()
    )
    return [track_out(t, album_uid=a.uid, artist_uid=artist_uid) for t in tracks]


async def album_out(s: AsyncSession, a: Album, *, with_tracks: bool = False) -> LibAlbumOut:
    uids = await _artist_uids(s, [a])
    count = (await _track_counts(s, [a.id])).get(a.id, 0)
    out = _album_brief(a, track_count=count, artist_uid=_artist_uid(uids, a))
    if with_tracks:
        out.tracks = await _album_tracks(s, a, _artist_uid(uids, a))
    return out


def _int_or_none(ref: str) -> int | None:
    try:
        return int(ref)
    except (TypeError, ValueError):
        return None


async def _album(s: AsyncSession, ref: str | int) -> Album:
    """Dual lookup: a public `uid` or the legacy deprecated int id (until stage 7)."""
    value = str(ref)
    legacy = _int_or_none(value)
    if legacy is not None:
        a = await s.get(Album, legacy)
    else:
        a = (await s.execute(select(Album).where(Album.uid == value))).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "Альбом не найден")
    return a


async def _full_track_out(s: AsyncSession, t: Track) -> LibTrackOut:
    album = await s.get(Album, t.album_id) if t.album_id else None
    uids = await _artist_uids(s, [album]) if album is not None else {}
    return track_out(
        t,
        album_uid=album.uid if album is not None else None,
        artist_uid=_artist_uid(uids, album) if album is not None else None,
    )


async def _track(s: AsyncSession, ref: str | int) -> Track:
    value = str(ref)
    legacy = _int_or_none(value)
    if legacy is not None:
        t = await s.get(Track, legacy)
    else:
        t = (await s.execute(select(Track).where(Track.uid == value))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Трек не найден")
    return t


async def _artist(s: AsyncSession, ref: str) -> Artist:
    a = (await s.execute(select(Artist).where(Artist.uid == str(ref)))).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "Исполнитель не найден")
    return a


async def _after_edit(s: AsyncSession, user: User, album_id: int | None) -> None:
    await s.commit()
    notify()
    album = await s.get(Album, album_id) if album_id is not None else None
    bus.publish(
        user.id,
        "library",
        {"kind": "changed", "album_id": album_id, "uid": album.uid if album is not None else None},
    )


async def _play_orders(s: AsyncSession, user_id: int) -> tuple[list[int], list[int]]:
    """Album ids by the user's playback journal: (most recently played, most played). Empty without links."""
    rows = (
        await s.execute(
            select(
                Track.album_id,
                func.max(PlayEvent.ts).label("last_ts"),
                func.count(PlayEvent.id).label("plays"),
            )
            .join(Track, Track.uid == PlayEvent.track_uid)
            .where(
                PlayEvent.user_id == user_id,
                PlayEvent.kind.in_(("start", "finish")),
                PlayEvent.track_uid.is_not(None),
                Track.album_id.is_not(None),
                Track.deleted_at.is_(None),
            )
            .group_by(Track.album_id)
        )
    ).all()
    recent = [
        int(album_id)
        for album_id, _ts, _n in sorted(rows, key=lambda r: (r[1], r[0]), reverse=True)
        if album_id is not None
    ]
    frequent = [
        int(album_id)
        for album_id, ts, n in sorted(rows, key=lambda r: (r[2], r[1], r[0]), reverse=True)
        if album_id is not None
    ]
    return recent, frequent


async def _starred_artist_ids(s: AsyncSession, user_id: int) -> list[int]:
    """Liked artists from the like/unlike journal, newest first: a bare Hub `uid` ref (the PWA of stage 6 names a
    library artist by it) or an `artist:<uid>`/`uid:` alias, like `_starred_album_ids` does for albums."""
    from app.favorites import snapshot

    states = await snapshot(s, user_id)
    liked_at: dict[str, datetime] = {}
    for st in states.values():
        if st.target_type != "artist" or not st.liked:
            continue
        refs = {x for x in (st.ref, *(st.aliases or [])) if x}
        uids = {r.split(":", 1)[1] for r in refs if r.startswith(("artist:", "uid:"))}
        uids |= {r for r in refs if not r.startswith(("dz:", "ref:", "nd:", "artist:", "uid:"))}
        if st.uid:
            uids.add(st.uid)
        when = st.since or st.event.ts
        for uid in uids:
            if uid not in liked_at or when > liked_at[uid]:
                liked_at[uid] = when
    if not liked_at:
        return []
    rows = list((await s.execute(select(Artist).where(Artist.uid.in_(liked_at)))).scalars())
    rows.sort(key=lambda a: liked_at[a.uid], reverse=True)
    return [a.id for a in rows]


async def _starred_album_ids(s: AsyncSession, user_id: int) -> list[int]:
    """Liked albums from the journal of like/unlike decisions (`favorites.snapshot`), newest first."""
    from app.favorites import snapshot

    states = await snapshot(s, user_id)
    dz: set[str] = set()
    uids: set[str] = set()
    for st in states.values():
        if st.target_type != "album" or not st.liked:
            continue
        refs = {x for x in (st.ref, *(st.aliases or [])) if x}
        dz |= {r.split(":", 1)[1] for r in refs if r.startswith("dz:")}
        uids |= {r.split(":", 1)[1] for r in refs if r.startswith(("album:", "uid:"))}
        uids |= {r for r in refs if not r.startswith(("dz:", "ref:", "nd:", "album:", "uid:"))}
        uids |= {st.uid} if st.uid else set()
    if not dz and not uids:
        return []
    conds = []
    if dz:
        conds.append(Album.deezer_album_id.in_(dz))
    if uids:
        conds.append(Album.uid.in_(uids))
    rows = (await s.execute(select(Album).where(or_(*conds)))).scalars().all()
    result = []
    for a in rows:
        liked_at = None
        for st in states.values():
            if (
                st.target_type == "album"
                and st.liked
                and (
                    (a.deezer_album_id and st.ref == f"dz:{a.deezer_album_id}")
                    or st.ref == a.uid
                    or any(x == f"album:{a.uid}" for x in (st.aliases or []))
                )
            ):
                liked_at = st.since
                break
        result.append((liked_at, a))
    result.sort(key=lambda pair: pair[0] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return [a.id for _, a in result]


async def _albums_by_ids(s: AsyncSession, ids: list[int]) -> list[Album]:
    if not ids:
        return []
    rows = {a.id: a for a in (await s.execute(select(Album).where(Album.id.in_(ids)))).scalars()}
    return [rows[i] for i in ids if i in rows]


def _sort_key(sort: str):
    if sort == "newest":
        return (Album.created_at.desc(), Album.id.desc())
    if sort == "alphabeticalByName":
        return (func.lower(Album.title), Album.title)
    if sort == "alphabeticalByArtist":
        return (func.lower(Album.album_artist), func.lower(Album.title))
    if sort == "random":
        return (func.random(),)
    return (Album.updated_at.desc(), Album.id.desc())


async def _album_list(
    session: AsyncSession, user: User, sort: str, offset: int, limit: int, q: str | None = None
) -> list[LibAlbumOut]:
    if sort in ("recent", "frequent"):
        recent, frequent = await _play_orders(session, user.id)
        ids = (recent if sort == "recent" else frequent)[offset : offset + limit]
        return await _albums_out(session, await _albums_by_ids(session, ids))
    if sort == "starred":
        ids = (await _starred_album_ids(session, user.id))[offset : offset + limit]
        return await _albums_out(session, await _albums_by_ids(session, ids))
    query = select(Album).order_by(*_sort_key(sort)).offset(max(offset, 0)).limit(limit)
    if q:
        cond = ilike_all(q)
        query = query.where(Album.search_text.ilike(cond) if cond is not None else false())
    rows = list((await session.execute(query)).scalars())
    return await _albums_out(session, rows)


async def _artists_out(
    s: AsyncSession, artists: list[Artist], counts: dict[int, int] | None = None
) -> list[LibArtistOut]:
    if counts is None:
        counts = await _album_counts(s, [a.id for a in artists])
    return [
        LibArtistOut(
            uid=a.uid,
            name=a.name,
            sort_name=a.sort_name or a.name,
            album_count=counts.get(a.id, 0),
            has_cover=a.cover_blob_id is not None,
        )
        for a in artists
    ]


async def _album_counts(s: AsyncSession, artist_ids: list[int]) -> dict[int, int]:
    if not artist_ids:
        return {}
    rows = (
        await s.execute(
            select(Album.artist_id, func.count(Album.id))
            .where(Album.artist_id.in_(artist_ids))
            .group_by(Album.artist_id)
        )
    ).all()
    return {int(artist_id): count for artist_id, count in rows if artist_id is not None}


@router.get("/api/library/home", response_model=LibHomeOut)
async def home(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The catalog rows of «Главная»: by playback journal, creation date and the ♥ journal."""
    recent, frequent = await _play_orders(s, user.id)
    newest = list(
        (await s.execute(select(Album).order_by(Album.created_at.desc(), Album.id.desc()).limit(HOME_ROW))).scalars()
    )
    starred = await _albums_by_ids(s, (await _starred_album_ids(s, user.id))[:HOME_ROW])
    top_artists = list(
        (
            await s.execute(
                select(Artist)
                .outerjoin(Album, Album.artist_id == Artist.id)
                .group_by(Artist.id)
                .order_by(func.count(Album.id).desc(), func.lower(Artist.name))
                .limit(HOME_ARTISTS)
            )
        ).scalars()
    )
    return LibHomeOut(
        recent=await _albums_out(s, await _albums_by_ids(s, recent[:HOME_ROW])),
        frequent=await _albums_out(s, await _albums_by_ids(s, frequent[:HOME_ROW])),
        newest=await _albums_out(s, newest),
        starred=await _albums_out(s, starred),
        artists=await _artists_out(s, top_artists),
    )


@router.get("/api/library/albums", response_model=list[LibAlbumOut])
async def list_albums(
    q: str | None = None,
    sort: str | None = None,
    type: str | None = Query(None, description="deprecated alias of `sort`"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session, scope="function"),
):
    chosen = sort or type or "updated_at"
    if chosen not in LIB_ALBUM_SORTS:
        raise HTTPException(422, f"sort: одно из {', '.join(LIB_ALBUM_SORTS)}")
    return await _album_list(session, user, chosen, offset, limit, q)


@router.get("/api/library/artists", response_model=list[LibArtistOut])
async def list_artists(
    q: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=200),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session, scope="function"),
):
    query = select(Artist).order_by(func.lower(Artist.name))
    if q:
        cond = ilike_all(q)
        query = query.where(Artist.name.ilike(cond) if cond is not None else false())
    rows = list((await session.execute(query.offset(offset).limit(limit))).scalars())
    return await _artists_out(session, rows)


@router.get("/api/library/artists/{artist_uid}", response_model=LibArtistPageOut)
async def get_artist(
    artist_uid: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    artist = await _artist(s, artist_uid)
    albums = list(
        (
            await s.execute(
                select(Album)
                .where(Album.artist_id == artist.id)
                .order_by(Album.year.desc().nulls_last(), func.lower(Album.title))
            )
        ).scalars()
    )
    count = (await _album_counts(s, [artist.id])).get(artist.id, 0)
    return LibArtistPageOut(
        artist=(await _artists_out(s, [artist], {artist.id: count}))[0],
        albums=await _albums_out(s, albums),
    )


@router.post("/api/library/artists/{artist_uid}/merge", response_model=LibArtistPageOut)
async def merge_artist(
    artist_uid: str,
    body: ArtistMergeIn,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    source = await _artist(s, artist_uid)
    target = await _artist(s, body.target_uid)
    await artists_lib.merge_artists(s, source, target)
    await _after_edit(s, user, None)
    albums = list((await s.execute(select(Album).where(Album.artist_id == target.id))).scalars())
    count = (await _album_counts(s, [target.id])).get(target.id, 0)
    return LibArtistPageOut(
        artist=(await _artists_out(s, [target], {target.id: count}))[0],
        albums=await _albums_out(s, albums),
    )


@router.post("/api/library/albums", response_model=LibAlbumOut)
async def create_album(
    body: AlbumCreateIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    a = Album(
        uid=str(uuid.uuid4()),
        title=body.title.strip(),
        album_artist=body.album_artist.strip(),
        year=body.year,
        genre=body.genre,
        release_type=body.release_type,
        created_by=user.id,
    )
    s.add(a)
    await s.flush()
    await artists_lib.link_album_artist(s, a)
    return await album_out(s, a, with_tracks=True)


def _mounted_path(rel: str) -> Path:
    """A bootstrap path strictly below MUSIC_ROOT: the mounted catalog is the only source admins may name."""
    root = get_settings().music_root.resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(400, f"Путь вне каталога музыки: {rel}")
    return path


@router.post("/api/library/import", response_model=LibraryImportOut)
async def import_mounted(
    body: LibraryImportIn,
    user: User = Depends(admin_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    """Administrative bootstrap import: the same `importer` and limits as uploads and downloads."""
    from app.library.importer import ImportSpec, import_file

    cfg = get_settings().cfg.uploads
    limit = cfg.max_file_mb * 1024 * 1024
    imported: list[LibTrackOut] = []
    events: list[tuple[int, str, int | None]] = []
    failed: list[str] = []
    for rel in body.paths:
        try:
            path = _mounted_path(rel)
        except HTTPException as e:
            failed.append(f"{rel}: {e.detail}")
            continue
        ext = path.suffix.lstrip(".").lower()
        if ext not in cfg.extensions:
            failed.append(f"{rel}: формат {ext or '?'} не поддерживается")
            continue
        if not path.is_file() or path.stat().st_size > limit:
            failed.append(f"{rel}: файла нет или он больше {cfg.max_file_mb} МБ")
            continue
        try:
            tags = await asyncio.to_thread(tagger.read_tags, path)
            info = await probe.probe(path)
            cover = await asyncio.to_thread(tagger.read_cover, path)
        except Exception as e:
            failed.append(f"{rel}: {e}")
            continue
        spec = ImportSpec(
            artist=tags.get("artist") or "Неизвестный исполнитель",
            title=tags.get("title") or path.stem,
            album=tags.get("album") or path.stem,
            album_artist=tags.get("album_artist") or tags.get("artist") or "Неизвестный исполнитель",
            year=tags.get("year"),
            release_type=tags.get("release_type") or "album",
            track_no=tags.get("track_no"),
            disc_no=tags.get("disc_no") or 1,
            genre=tags.get("genre"),
            isrc=tags.get("isrc"),
            explicit=tags.get("explicit"),
            cover=cover,
            source="manual",
            source_url=rel,
            duration_s=round(info.duration_s) if info.duration_s else None,
            bitrate_kbps=info.bitrate_kbps,
            codec=info.codec,
            added_by=user.id,
        )
        try:
            # one savepoint per file: a broken file does not abort the files already staged in this transaction
            async with s.begin_nested():
                track = await import_file(s, path, spec)
        except Exception as e:
            log.exception("bootstrap import failed: %s", rel)
            failed.append(f"{rel}: {e}")
            continue
        album = await s.get(Album, track.album_id) if track.album_id else None
        events.append((track.id, track.uid, album.id if album is not None else None))
        imported.append(
            track_out(
                track,
                album_uid=album.uid if album is not None else None,
                artist_uid=None,
            )
        )
    await s.commit()
    notify()
    for track_id, track_uid, album_id in events:
        bus.publish(
            user.id,
            "library",
            {"kind": "imported", "track_id": track_id, "album_id": album_id, "uid": track_uid},
        )
    return LibraryImportOut(imported=len(imported), failed=failed, tracks=imported)


@router.get("/api/library/track-source", response_model=TrackSourceOut)
async def track_source(
    uid: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    t = (
        await s.execute(select(Track).where(Track.uid == uid, Track.deleted_at.is_(None)).limit(1))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Трек не найден")
    return TrackSourceOut(source=t.source, source_url=t.source_url, acquired_at=t.acquired_at)


@router.get("/api/library/albums/{album_ref}", response_model=LibAlbumOut)
async def get_album(
    album_ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    return await album_out(s, await _album(s, album_ref), with_tracks=True)


@router.patch("/api/library/albums/{album_ref}", response_model=LibAlbumOut)
async def patch_album(
    album_ref: str,
    body: AlbumPatchIn,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    a = await _album(s, album_ref)
    await editor.update_album(s, a, **body.model_dump(exclude_unset=True))
    await _after_edit(s, user, a.id)
    return await album_out(s, a, with_tracks=True)


@router.put("/api/library/albums/{album_ref}/cover", response_model=LibAlbumOut)
async def put_cover(
    album_ref: str,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    a = await _album(s, album_ref)
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Картинка слишком большая")
    try:
        await editor.set_album_cover(s, a, data)
    except Exception as e:
        raise HTTPException(400, f"Не удалось прочитать картинку: {e}") from e
    await _after_edit(s, user, a.id)
    return await album_out(s, a, with_tracks=True)


@router.get("/api/library/albums/{album_ref}/cover")
async def get_cover(
    album_ref: str,
    size: int | None = Query(None, ge=32, le=1000),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    a = await _album(s, album_ref)
    data = await covers.cached_or_resized(s, a, size)
    if data is None:
        raise HTTPException(404, "Нет обложки")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


async def _source_album_ids(s: AsyncSession, body: AlbumMergeIn) -> list[int]:
    ids = set(body.source_ids)
    for ref in body.source_uids:
        ids.add((await _album(s, ref)).id)
    return sorted(ids)


@router.post("/api/library/albums/merge", response_model=LibAlbumOut)
async def merge(
    body: AlbumMergeIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    target_ref = body.target_uid or (str(body.target_id) if body.target_id is not None else None)
    if target_ref is None:
        raise HTTPException(422, "Укажите target_uid или target_id")
    target = await _album(s, target_ref)
    await editor.merge_albums(s, await _source_album_ids(s, body), target)
    await _after_edit(s, user, target.id)
    return await album_out(s, target, with_tracks=True)


async def _move_track_ids(s: AsyncSession, body: TracksMoveIn) -> list[int]:
    ids = set(body.track_ids)
    for ref in body.track_uids:
        ids.add((await _track(s, ref)).id)
    return sorted(ids)


@router.post("/api/library/tracks/move", response_model=LibAlbumOut)
async def move(
    body: TracksMoveIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    target_ref = body.album_uid or (str(body.album_id) if body.album_id is not None else None)
    if target_ref is None:
        raise HTTPException(422, "Укажите album_uid или album_id")
    target = await _album(s, target_ref)
    await editor.move_tracks(s, await _move_track_ids(s, body), target)
    await _after_edit(s, user, target.id)
    return await album_out(s, target, with_tracks=True)


@router.get("/api/library/tracks/{track_ref}", response_model=LibTrackOut)
async def get_track(
    track_ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    return await _full_track_out(s, await _track(s, track_ref))


@router.patch("/api/library/tracks/{track_ref}", response_model=LibTrackOut)
async def patch_track(
    track_ref: str,
    body: TrackPatchIn,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    t = await _track(s, track_ref)
    await editor.update_track(s, t, **body.model_dump(exclude_unset=True))
    out = await _full_track_out(s, t)
    await _after_edit(s, user, t.album_id)
    return out


@router.delete("/api/library/tracks/{track_ref}")
async def delete_track(
    track_ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    t = await _track(s, track_ref)
    album_id = t.album_id
    await editor.trash_track(s, t)
    await _after_edit(s, user, album_id)
    return {"ok": True}


@router.get("/api/library/tracks/{track_ref}/download")
async def download_track(
    track_ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    """A tagged copy of the blob (export/materialization); ordinary edits never rewrite the object."""
    t = await _track(s, track_ref)
    if t.deleted_at is not None:
        raise HTTPException(404, "Трек не найден")
    try:
        path = await export.materialize(s, t)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(path, filename=path.name, background=BackgroundTask(export.cleanup, path))


@router.post("/api/library/tracks/{track_ref}/restore")
async def restore_track(
    track_ref: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    t = await _track(s, track_ref)
    if t.deleted_at is None:
        return {"ok": True}
    await editor.restore_track(s, t)
    await _after_edit(s, user, t.album_id)
    return {"ok": True}


@router.get("/api/starred", response_model=StarredOut)
async def starred(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The user's ♥, served by the Hub only (`hub_liked_v1`)."""
    from app.api.schemas import FavoriteTarget
    from app.favorites import AliasIndex, imported_targets, snapshot
    from app.imports.apply import pending_like_refs
    from app.models import FavoriteSubject

    states = await snapshot(s, user.id)
    index = AliasIndex([x.event for x in states.values()], list((await s.execute(select(FavoriteSubject))).scalars()))
    for state in states.values():
        index.join(state.target_type, set(state.aliases))
    imported = {index.root(k) for k in await imported_targets(s, user.id)}
    subjects = []
    for state in states.values():
        provenance = (
            "import" if index.root((state.target_type, min(state.aliases or [state.key]))) in imported else "own"
        )
        subjects.append(
            dict(
                target_type=state.target_type,
                key=state.key,
                ref=state.ref,
                uid=state.uid,
                liked=state.liked,
                liked_since=state.since,
                target_data=state.data,
                event_id=state.event.event_id,
                aliases=state.aliases,
                provenance=provenance,
            )
        )
    refs = set(await pending_like_refs(s, user.id))
    for ref in sorted(refs):
        key = index.key(FavoriteTarget(ref=ref))
        if key not in states:
            subjects.append(
                dict(
                    target_type="track",
                    key=key[1],
                    ref=ref,
                    uid=None,
                    liked=True,
                    aliases=[f"ref:{ref}"],
                    provenance="import",
                )
            )
    track_uids: list[str] = []
    for state in states.values():
        if state.liked and state.target_type == "track" and state.uid:
            track_uids.append(state.uid)
    refs.update(x.ref for x in states.values() if x.target_type == "track" and x.liked and x.ref and not x.uid)
    return StarredOut(
        track_uids=list(dict.fromkeys(track_uids)),
        refs=sorted(refs),
        subjects=subjects,
    )


@router.get("/api/library/liked", response_model=LibLikedOut)
async def liked(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """Tracks, albums and artists the user ♥: own decisions override imported ones, newest first."""
    from app.favorites import snapshot

    states = [x for x in (await snapshot(s, user.id)).values() if x.liked]
    states.sort(key=lambda x: (x.since or x.event.ts, x.event.id or 0), reverse=True)
    track_uids = {x.uid for x in states if x.target_type == "track" and x.uid}
    tracks_ = (
        list(
            (
                await s.execute(
                    select(Track).where(Track.uid.in_(track_uids), Track.deleted_at.is_(None)).order_by(Track.id.desc())
                )
            ).scalars()
        )
        if track_uids
        else []
    )
    out_tracks = [await _full_track_out(s, t) for t in tracks_]
    album_ids = await _starred_album_ids(s, user.id)
    albums = [a for a in (await s.execute(select(Album).where(Album.id.in_(album_ids)))).scalars()] if album_ids else []
    albums.sort(key=lambda a: album_ids.index(a.id))
    album_out_list = await _albums_out(s, albums)
    artist_ids = await _starred_artist_ids(s, user.id)
    artists = (
        [a for a in (await s.execute(select(Artist).where(Artist.id.in_(artist_ids)))).scalars()] if artist_ids else []
    )
    artists.sort(key=lambda a: artist_ids.index(a.id))
    counts = (
        dict(
            (
                await s.execute(
                    select(Album.artist_id, func.count(Album.id))
                    .where(Album.artist_id.in_([a.id for a in artists]))
                    .group_by(Album.artist_id)
                )
            ).all()
        )
        if artists
        else {}
    )
    return LibLikedOut(
        tracks=out_tracks,
        albums=album_out_list,
        artists=[
            LibArtistOut(
                uid=a.uid,
                name=a.name,
                sort_name=a.sort_name,
                album_count=int(counts.get(a.id, 0) or 0),
                has_cover=a.cover_blob_id is not None,
            )
            for a in artists
        ],
    )


@router.post("/api/star")
async def star(
    body: StarIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session, scope="function")
):
    from app.api.schemas import FavoriteTarget
    from app.favorites import command

    targets = list(body.targets)
    targets += [FavoriteTarget(uid=x) for x in body.uids]
    if body.refs and not body.unstar:
        raise HTTPException(400, "Чтобы отметить трек каталога, поставьте его в загрузку с ♥")
    targets += [FavoriteTarget(ref=x) for x in body.refs]
    try:
        if body.catalog_album_ref:
            src, id_ = split_ref(body.catalog_album_ref)
            album_filter = Album.deezer_album_id == id_ if src == "dz" else Album.id == -1
            album = (await session.execute(select(Album).where(album_filter).limit(1))).scalar_one_or_none()
            if album is None:
                raise HTTPException(409, "Этого альбома ещё нет в библиотеке — сначала скачайте его")
            targets.append(
                FavoriteTarget(
                    target_type="album",
                    ref=body.catalog_album_ref,
                    uid=album.uid,
                    target_data={"artist": album.album_artist},
                )
            )
        events = await command(
            session,
            user,
            targets,
            liked=not body.unstar,
            operation=body.event_id,
            ts=body.ts,
            client=body.client,
            context=body.context,
        )
        # Telemetry may have stored the same decision before the command. Pending downloads
        # still need cancellation, but an older unlike must not cancel a newer like.
        if body.unstar and body.event_id:
            from app.favorites import clear_pending, snapshot

            states = await snapshot(session, user.id)
            for target in targets:
                if target.target_type != "track" or not target.ref:
                    continue
                state = next((x for x in states.values() if x.target_type == "track" and x.ref == target.ref), None)
                if state and not state.liked:
                    await clear_pending(session, user.id, target.ref)
    except Exception:
        # Preserve successful chunks of a bulk request even if a later chunk fails.
        await session.commit()
        raise
    await session.commit()
    bus.publish(user.id, "library", {"kind": "unstarred" if body.unstar else "starred"})
    return {"ok": True, "event_ids": [e.event_id for e in events]}
