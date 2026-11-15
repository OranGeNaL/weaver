"""What a matched import row does in HomeMusic: ♥ in the Hub, Hub playlists in the service's order, downloads.

- ♥: the Hub journals the import's liked songs itself (`sync_likes`), nearest to the service's order from the oldest
  like; a song that arrives later (a download) gets its ♥ then.
- Playlists: the whole playlist is rewritten in the service's order (`playlists.replace_tracks`) from what the
  library has.
- Downloads are the Hub's own business (`DownloadItem.query_json["import"]`): low priority, not shown as downloads.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import split_ref
from app.imports.resolve import Resolution
from app.jobs.queue import PRIORITY_IMPORT, enqueue
from app.library.owned import ACTIVE_STATUSES, find_existing
from app.matching.normalize import normalize_artist
from app.models import (
    Album,
    DownloadBatch,
    DownloadItem,
    Import,
    ImportCollection,
    ImportItem,
    Track,
    User,
)
from app.providers.base import TrackQuery

log = logging.getLogger(__name__)

# --- decisions ---------------------------------------------------------------------------------------------


async def decide(s: AsyncSession, imp: Import, it: ImportItem, kind: str, r: Resolution | None) -> None:
    """A matched row (`r`; None: owned since the fetch) → its status and, by the import's mode, a download."""
    if r is not None:
        it.match, it.ref, it.score = r.match, r.ref, r.score
        if r.track is not None:
            it.track_id = r.track.id
    if kind == "dislikes":
        it.status = "skipped"  # never downloaded, never ♥: «Не нравится» for mixes and «Моя волна»
        track = await s.get(Track, it.track_id) if it.track_id else None
        if it.match != "not_found" and (it.ref or (track and track.uid)):
            from app.plays import add_dislike

            raw = it.raw_json or {}
            await add_dislike(
                s,
                imp.user_id,
                ref=it.ref,
                uid=track.uid if track else None,
                artist=raw.get("artist"),
                title=raw.get("title"),
                source="yandex_import",
            )
        return
    if kind == "artists":
        it.status = "done" if it.match != "not_found" else "not_found"  # ★ at the end, if the library has them
        return
    if kind == "albums":
        if it.match == "not_found":
            it.status = "not_found"
        elif await album_owned(s, it.ref):
            it.status = "done"
        elif imp.mode == "full" and (imp.options_json or {}).get("download_albums"):
            await queue_album(s, imp, it)
        else:
            it.status = "waiting"
        return
    if it.match == "owned":
        it.status = "done"
    elif it.match == "not_found":
        it.status = "not_found"
    elif imp.mode == "full":
        await queue_track(s, imp, it, kind)
    else:
        it.status = "waiting"


async def _batch(s: AsyncSession, imp: Import) -> DownloadBatch:
    opts = dict(imp.options_json or {})
    batch = await s.get(DownloadBatch, opts["batch_id"]) if opts.get("batch_id") else None
    if batch is None:
        batch = DownloadBatch(user_id=imp.user_id, origin="import", title=f"Импорт: {imp.account or imp.source}")
        s.add(batch)
        await s.flush()
        imp.options_json = {**opts, "batch_id": batch.id}
    return batch


def track_query(raw: dict[str, Any]) -> TrackQuery:
    main = (raw.get("artists") or [raw.get("artist") or ""])[0]
    return TrackQuery(
        artist=main or raw.get("artist") or "",
        title=raw.get("title") or "",
        album=raw.get("album"),
        duration_s=raw.get("duration_s"),
    )


async def new_download(s: AsyncSession, imp: Import, query_json: dict[str, Any], priority: int) -> DownloadItem:
    batch = await _batch(s, imp)
    item = DownloadItem(batch_id=batch.id, user_id=imp.user_id, query_json=query_json)
    s.add(item)
    await s.flush()
    await enqueue(s, "download_item", {"item_id": item.id}, priority=priority)
    return item


async def queue_track(
    s: AsyncSession, imp: Import, it: ImportItem, kind: str, *, priority: int = PRIORITY_IMPORT
) -> bool:
    """Download a matched track (once per import: a song in likes and in a playlist is one download)."""
    if not it.ref:
        return False
    twin = (
        await s.execute(
            select(ImportItem.download_item_id)
            .where(ImportItem.import_id == imp.id, ImportItem.ref == it.ref, ImportItem.download_item_id.is_not(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if twin is not None:
        it.download_item_id, it.status = twin, "queued"
        return True
    raw = it.raw_json or {}
    src, id_ = split_ref(it.ref)
    meta: dict[str, Any] = {"track_ref": it.ref}
    if src == "dz":
        meta["deezer_track_id"] = id_
    if raw.get("artists"):
        meta["artists"] = raw["artists"]
    qj: dict[str, Any] = {"query": track_query(raw).model_dump(), "meta": meta, "import": {"id": imp.id, "item": it.id}}
    dl = await new_download(s, imp, qj, priority)
    it.download_item_id, it.status = dl.id, "queued"
    return True


async def queue_album(s: AsyncSession, imp: Import, it: ImportItem) -> int:
    """A liked album, downloaded whole (its downloads point back to the row: `import.album`)."""
    from app.catalog.deezer import CatalogNotFound
    from app.jobs.downloads import get_catalog_album, meta_from_catalog, query_from_track
    from app.jobs.queue import PermanentError

    try:
        album = await get_catalog_album(it.ref or "")
    except (CatalogNotFound, PermanentError) as e:
        log.warning("import %s: album %s: %r", imp.id, it.ref, e)
        it.status = "failed"
        return 0
    n = 0
    for t in album.tracks or []:
        if await find_existing(
            s,
            artist=t.artist,
            title=t.title,
            duration_s=t.duration_s,
            isrc=t.isrc,
            deezer_track_id=t.id if t.source == "dz" else None,
        ):
            continue
        qj = {
            "query": query_from_track(t).model_dump(),
            "meta": meta_from_catalog(t, album),
            "import": {"id": imp.id, "album": it.id},
        }
        await new_download(s, imp, qj, PRIORITY_IMPORT)
        n += 1
    it.status = "queued" if n else "done"
    return n


# --- state of the downloads ----------------------------------------------------------------------------------


def _import_downloads(imp: Import):
    return select(DownloadItem).where(cast(DownloadItem.query_json["import"]["id"].astext, Integer) == imp.id)


async def active_downloads(s: AsyncSession, imp: Import) -> int:
    return (
        await s.execute(
            select(func.count()).select_from(
                _import_downloads(imp).where(DownloadItem.status.in_(ACTIVE_STATUSES)).subquery()
            )
        )
    ).scalar_one()


async def refresh_items(s: AsyncSession, imp: Import) -> None:
    """Rows follow their downloads; a waiting row whose track got into the library (♥, a search) is done."""
    rows = (
        await s.execute(
            select(ImportItem, DownloadItem)
            .join(DownloadItem, DownloadItem.id == ImportItem.download_item_id)
            .where(ImportItem.import_id == imp.id, ImportItem.status == "queued")
        )
    ).all()
    for it, dl in rows:
        if dl.status == "done":
            it.status, it.track_id = "done", dl.track_id or it.track_id
        elif dl.status in ("failed", "needs_review", "cancelled"):
            it.status = "failed"
    albums = (
        (
            await s.execute(
                select(ImportItem)
                .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
                .where(ImportItem.import_id == imp.id, ImportItem.status == "queued", ImportCollection.kind == "albums")
            )
        )
        .scalars()
        .all()
    )
    for it in albums:
        left = (
            await s.execute(
                select(func.count()).select_from(
                    select(DownloadItem.id)
                    .where(
                        cast(DownloadItem.query_json["import"]["album"].astext, Integer) == it.id,
                        DownloadItem.status.in_(ACTIVE_STATUSES),
                    )
                    .subquery()
                )
            )
        ).scalar_one()
        if not left:
            it.status = "done"
    waiting = (
        (
            await s.execute(
                select(ImportItem)
                .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
                .where(
                    ImportItem.import_id == imp.id,
                    ImportItem.status == "waiting",
                    ImportCollection.kind.in_(("likes", "playlist")),
                )
            )
        )
        .scalars()
        .all()
    )
    for it in waiting:
        raw = it.raw_json or {}
        src, id_ = split_ref(it.ref) if it.ref else ("", "")
        lib = await find_existing(
            s,
            artist=raw.get("artist") or "",
            title=raw.get("title") or "",
            duration_s=raw.get("duration_s"),
            deezer_track_id=id_ if src == "dz" else None,
        )
        if lib is not None:
            it.status, it.track_id = "done", lib.id
    await s.flush()


# --- Navidrome ------------------------------------------------------------------------------------------------


async def _track_uids(s: AsyncSession, items: list[ImportItem]) -> dict[int, str]:
    """import item id -> Hub track uid of its library track."""
    track_ids = {i.track_id for i in items if i.track_id}
    if not track_ids:
        return {}
    uids = dict(
        (await s.execute(select(Track.id, Track.uid).where(Track.id.in_(track_ids), Track.deleted_at.is_(None)))).all()
    )
    out: dict[int, str] = {}
    for i in items:
        uid = uids.get(i.track_id) if i.track_id is not None else None
        if uid:
            out[i.id] = uid
    return out


def playlist_order(items: list[ImportItem], uids: dict[int, str]) -> list[str]:
    """Tracks of a playlist in the service's order: what the library has, without the rest."""
    return [
        uids[i.id] for i in sorted(items, key=lambda i: i.position) if i.id in uids and i.status not in ("skipped",)
    ]


def like_order(items: list[ImportItem], uids: dict[int, str]) -> list[str]:
    """Liked songs from the oldest like to the newest (the service lists the newest first), each once."""
    out: dict[str, None] = {}
    for i in sorted(items, key=lambda i: -i.position):
        if i.id in uids and i.status == "done":
            out.setdefault(uids[i.id], None)
    return list(out)


async def sync_playlists(s: AsyncSession, imp: Import) -> None:
    """The import's playlists as Hub playlists in the service's order (the same playlist every run)."""
    from app import playlists as playlists_lib

    colls = (
        (
            await s.execute(
                select(ImportCollection).where(
                    ImportCollection.import_id == imp.id,
                    ImportCollection.kind == "playlist",
                    ImportCollection.selected.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not colls:
        return
    for c in colls:
        items = list((await s.execute(select(ImportItem).where(ImportItem.collection_id == c.id))).scalars())
        want = playlist_order(items, await _track_uids(s, items))
        existing = c.playlist_uid
        playlist = await playlists_lib.get_owned(s, imp.user_id, existing) if existing else None
        if playlist is None:
            playlist = await playlists_lib.create(s, imp.user_id, c.title)
        if want != list(c.track_uids or []):
            await playlists_lib.replace_tracks(s, playlist, want)
        c.playlist_uid, c.track_uids = playlist.uid, want
    await s.flush()


async def _likes(s: AsyncSession, imp: Import) -> list[ImportItem]:
    return list(
        (
            await s.execute(
                select(ImportItem)
                .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
                .where(
                    ImportItem.import_id == imp.id,
                    ImportCollection.kind == "likes",
                    ImportCollection.selected.is_(True),
                )
            )
        ).scalars()
    )


async def sync_likes(s: AsyncSession, imp: Import) -> None:
    """♥ of the import's likes, the oldest like first: a local decision per track the library has. A song the user
    has unstarred since is left alone; a song that downloads later gets its ♥ when it is in the library."""
    from app.api.schemas import FavoriteTarget
    from app.favorites import record, snapshot

    items = await _likes(s, imp)
    uids = await _track_uids(s, items)
    opts = dict(imp.options_json or {})
    want = like_order(items, uids)
    states = await snapshot(s, imp.user_id)
    liked = {st.uid for st in states.values() if st.target_type == "track" and st.liked and st.uid}
    unliked = {st.uid for st in states.values() if st.target_type == "track" and not st.liked and st.uid}
    added: list[str] = []
    for uid in want:
        if uid in liked or uid in unliked:
            continue
        await record(
            s,
            imp.user_id,
            FavoriteTarget(uid=uid),
            True,
            operation=f"import:{imp.id}:{uid}",
            client="import",
            context="import:likes",
        )
        added.append(uid)
    imp.options_json = {**opts, "liked_uids": want}
    await s.flush()
    if added:
        from app.events import bus

        bus.publish(imp.user_id, "library", {"kind": "starred", "track_uids": added})


async def album_owned(s: AsyncSession, ref: str | None) -> bool:
    if not ref:
        return False
    src, id_ = split_ref(ref)
    if src != "dz":
        return False
    return (
        await s.execute(
            select(Track.id).join(Album, Album.id == Track.album_id).where(Album.deezer_album_id == id_).limit(1)
        )
    ).scalar_one_or_none() is not None


async def star_albums_artists(s: AsyncSession, imp: Import) -> None:
    """★ liked albums and artists the Hub library has: a local album/artist ♥ decision."""
    from app.api.schemas import FavoriteTarget
    from app.favorites import snapshot
    from app.models import Artist, Track

    rows = (
        await s.execute(
            select(ImportItem, ImportCollection.kind)
            .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
            .where(
                ImportItem.import_id == imp.id,
                ImportCollection.kind.in_(("albums", "artists")),
                ImportCollection.selected.is_(True),
                ImportItem.status.in_(("done", "waiting", "queued")),
            )
        )
    ).all()
    if not rows:
        return
    user = await s.get(User, imp.user_id)
    if user is None:
        return
    states = await snapshot(s, imp.user_id)
    liked = {(st.target_type, st.uid) for st in states.values() if st.liked and st.uid}
    album_uids: list[str] = []
    artist_uids: list[str] = []
    for it, kind in rows:
        if kind == "albums" and it.ref and split_ref(it.ref)[0] == "dz":
            album = (
                await s.execute(
                    select(Album)
                    .where(
                        Album.deezer_album_id == split_ref(it.ref)[1],
                        Album.id.in_(select(Track.album_id).where(Track.deleted_at.is_(None))),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if album is None or ("album", album.uid) in liked:
                continue
            from app.favorites import command

            await command(
                s,
                user,
                [FavoriteTarget(target_type="album", ref=it.ref, uid=album.uid, target_data=it.raw_json)],
                liked=True,
                operation=f"import:{imp.id}:album:{album.uid}",
                client="import",
                context="import:albums",
            )
            album_uids.append(album.uid)
        elif kind == "artists":
            name = (it.raw_json or {}).get("name") or ""
            want = normalize_artist(name).text
            if not name:
                continue
            found = list((await s.execute(select(Artist).where(Artist.name_norm == want))).scalars())
            artist = found[0] if len(found) == 1 else None
            if artist is None or ("artist", artist.uid) in liked:
                continue
            from app.favorites import command

            await command(
                s,
                user,
                [FavoriteTarget(target_type="artist", ref=it.ref, uid=artist.uid, target_data={"name": name})],
                liked=True,
                operation=f"import:{imp.id}:artist:{artist.uid}",
                client="import",
                context="import:artists",
            )
            artist_uids.append(artist.uid)
    if album_uids or artist_uids:
        opts = imp.options_json or {}
        imp.options_json = {
            **opts,
            "liked_album_ids": list(dict.fromkeys([*(opts.get("liked_album_ids") or []), *album_uids])),
            "liked_artist_ids": list(dict.fromkeys([*(opts.get("liked_artist_ids") or []), *artist_uids])),
        }
    await s.flush()


async def cancel_downloads(s: AsyncSession, imp: Import) -> int:
    """The import's own downloads that have not started (one the user asked for explicitly stays)."""
    n = 0
    for dl in (await s.execute(_import_downloads(imp).where(DownloadItem.status == "queued"))).scalars():
        if not (dl.query_json or {}).get("requested"):
            dl.status = "cancelled"
            n += 1
    await s.flush()
    return n


# --- ♥ of tracks the library does not have yet -----------------------------------------------------------------


def _user_likes(user_id: int):
    return (
        select(ImportItem)
        .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
        .join(Import, Import.id == ImportItem.import_id)
        .where(
            Import.user_id == user_id,
            ImportCollection.kind == "likes",
            ImportCollection.selected.is_(True),
            Import.status.in_(("running", "done")),
            ImportItem.ref.is_not(None),
        )
    )


async def pending_like_refs(s: AsyncSession, user_id: int) -> list[str]:
    """Catalog refs the user liked in another service that wait for a listen or a download (shown with ♥)."""
    rows = (await s.execute(_user_likes(user_id).where(ImportItem.status.in_(("waiting", "queued"))))).scalars()
    return list(dict.fromkeys(i.ref for i in rows if i.ref))


async def unlike_refs(s: AsyncSession, user_id: int, refs: list[str]) -> None:
    """♥ taken off a track liked in another service: it is no longer liked nor downloaded for that."""
    for it in (
        await s.execute(
            _user_likes(user_id).where(ImportItem.ref.in_(refs), ImportItem.status.in_(("waiting", "queued")))
        )
    ).scalars():
        if it.download_item_id:
            dl = await s.get(DownloadItem, it.download_item_id)
            if dl is not None and dl.status == "queued" and not (dl.query_json or {}).get("requested"):
                dl.status = "cancelled"
            elif dl is not None:
                dl.query_json = {k: v for k, v in (dl.query_json or {}).items() if k != "star"}
        it.status = "skipped"
    await s.flush()
