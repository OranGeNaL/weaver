"""Listening data for mixes and «Моя волна» (MIXES.md 3): events from the app and the PWA, scrobbles of Hub clients,
«Не нравится».

- An event names its track by a Hub `track_uid` and/or a catalog `ref`; the Navidrome `nd_song_id` stays a deprecated
  alias until stage 7. The Hub fills in the other identifiers where it knows them.
- `event_id` is the client's: a batch sent again (the app retries when offline) stores nothing twice.
- A client scrobble is a `finish` with `scrobble=true`/`origin=client` and the Hub track uid. The app's own measured
  events of the same song near it win: a scrobble is not counted next to them, and a scrobble that arrived first is
  marked `superseded_scrobble` when they come (`DEDUP_WINDOW` plus the track's length).
- ♥ that an import of another service set are not the user's own (M4): `imported_likes` lists them for callers
  that still work with Navidrome ids; the profile itself reads `favorites.imported_targets`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import Result, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import PlayEventIn, ScrobbleIn
from app.catalog.models import split_ref
from app.models import (
    CatalogCache,
    Dislike,
    DownloadItem,
    Import,
    ImportItem,
    PlayEvent,
    Track,
    utcnow,
)

log = logging.getLogger(__name__)

PLAY_KINDS = ("start", "skip", "finish")
# every event of a playback attempt: a checkpoint carries the cumulative `listened_ms` that may cross the threshold
PLAYBACK_KINDS = ("start", "progress", "interrupted", "error", "skip", "finish")
DEDUP_WINDOW = timedelta(minutes=5)
MAX_FUTURE = timedelta(hours=1)  # a client clock this far ahead is wrong: the event gets the time it came in


def _rowcount(res: Result[Any]) -> int:
    """Rows an INSERT/DELETE touched. `execute()` is typed as `Result`; the database returns `CursorResult`."""
    return int(getattr(res, "rowcount", 0) or 0)


async def _favorites_snapshot(session: AsyncSession, user_id: int, now: datetime) -> dict[Any, Any]:
    """The user's ♥ state; imported here because `app.favorites` imports this module."""
    from app.favorites import snapshot

    return await snapshot(session, user_id, now)


# --- ids -----------------------------------------------------------------------------------------------------


async def _song_of_ref(session: AsyncSession, ref: str) -> str | None:
    """The Navidrome song of a catalog track the library has."""
    try:
        src, id_ = split_ref(ref)
    except ValueError:
        src, id_ = "", ""
    if src == "dz":
        nd = (
            await session.execute(
                select(Track.nd_song_id)
                .where(Track.deezer_track_id == id_, Track.nd_song_id.is_not(None), Track.deleted_at.is_(None))
                .order_by(Track.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if nd:
            return nd
    # the download (any ref: hitmoz, a provider'session file) or the import row that brought the file in
    by_download = (
        select(Track.nd_song_id)
        .join(DownloadItem, DownloadItem.track_id == Track.id)
        .where(
            DownloadItem.query_json["meta"]["track_ref"].astext == ref,
            Track.nd_song_id.is_not(None),
            Track.deleted_at.is_(None),
        )
        .order_by(DownloadItem.id.desc())
        .limit(1)
    )
    nd = (await session.execute(by_download)).scalar_one_or_none()
    if nd:
        return nd
    by_import = (
        select(Track.nd_song_id)
        .join(ImportItem, ImportItem.track_id == Track.id)
        .where(ImportItem.ref == ref, Track.nd_song_id.is_not(None), Track.deleted_at.is_(None))
        .order_by(ImportItem.id.desc())
        .limit(1)
    )
    return (await session.execute(by_import)).scalar_one_or_none()


async def _ref_of_song(s: AsyncSession, nd_song_id: str) -> str | None:
    """The catalog track of a library song."""
    t = (
        await s.execute(
            select(Track)
            .where(Track.nd_song_id == nd_song_id, Track.deleted_at.is_(None))
            .order_by(Track.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if t is None:
        return None
    if t.deezer_track_id:
        return f"dz:{t.deezer_track_id}"
    ref = (
        await s.execute(
            select(DownloadItem.query_json["meta"]["track_ref"].astext)
            .where(DownloadItem.track_id == t.id, DownloadItem.query_json["meta"]["track_ref"].astext.is_not(None))
            .order_by(DownloadItem.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ref:
        return str(ref)
    return (
        await s.execute(
            select(ImportItem.ref)
            .where(ImportItem.track_id == t.id, ImportItem.ref.is_not(None))
            .order_by(ImportItem.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _uid_of_library_ref(s: AsyncSession, ref: str) -> str | None:
    """`lib:<uid>` or a bare track `uid` of the Hub catalog: the uid itself when the track exists."""
    uid = ref[4:] if ref.startswith("lib:") else ref
    found = (
        await s.execute(select(Track.uid).where(Track.uid == uid, Track.deleted_at.is_(None)).limit(1))
    ).scalar_one_or_none()
    return cast("str | None", found)


async def track_uid_of(s: AsyncSession, ref: str | None, nd_song_id: str | None) -> str | None:
    """The Hub `uid` of a library track named by `lib:<uid>`, a bare `uid`, a catalog ref and/or a deprecated
    Navidrome song id."""
    if nd_song_id:
        uid = (
            await s.execute(
                select(Track.uid)
                .where(Track.nd_song_id == nd_song_id, Track.deleted_at.is_(None))
                .order_by(Track.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if uid:
            return uid
    if ref:
        if ref.startswith("lib:") or ":" not in ref:
            if uid := await _uid_of_library_ref(s, ref):
                return uid
        else:
            src, id_ = split_ref(ref)
            if src == "dz":
                uid = (
                    await s.execute(
                        select(Track.uid)
                        .where(Track.deezer_track_id == id_, Track.deleted_at.is_(None))
                        .order_by(Track.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if uid:
                    return uid
            song = await _song_of_ref(s, ref)
            if song:
                return await track_uid_of(s, None, song)
    return None


async def resolve_song_ids(ids: list[str]) -> list[str]:
    """The context's song ids as smart shuffle names them: `lib:<uid>` of a library track becomes the track uid,
    a bare uid and a catalog ref stay as they are. One lookup for the whole list."""
    lib = [i[4:] for i in ids if i.startswith("lib:")]
    if not lib:
        return list(ids)
    from app.db import session_scope

    found: set[str] = set()
    async with session_scope() as s:
        for k in range(0, len(lib), 500):
            found |= set(
                (await s.execute(select(Track.uid).where(Track.uid.in_(lib[k : k + 500]), Track.deleted_at.is_(None))))
                .scalars()
                .all()
            )
    return [i[4:] if i.startswith("lib:") and i[4:] in found else i for i in ids]


async def track_ids_of_uid(s: AsyncSession, uid: str) -> tuple[str | None, str | None]:
    """The catalog ref and the deprecated Navidrome song of a Hub track uid."""
    t = (
        await s.execute(select(Track).where(Track.uid == uid, Track.deleted_at.is_(None)).limit(1))
    ).scalar_one_or_none()
    if t is None:
        return None, None
    ref = f"dz:{t.deezer_track_id}" if t.deezer_track_id else None
    if ref is None and t.nd_song_id:
        ref = await _ref_of_song(s, t.nd_song_id)
    return ref or f"lib:{uid}", t.nd_song_id


@dataclass
class Ids:
    """Fills in `ref` ↔ `nd_song_id` (cached for one batch)."""

    s: AsyncSession
    _songs: dict[str, str | None] = field(default_factory=dict)
    _refs: dict[str, str | None] = field(default_factory=dict)

    async def prime(self, pairs: Iterable[tuple[str | None, str | None]]) -> None:
        """Complete old partial events in batches, never one SQL query per historical event."""
        pairs = list(pairs)
        refs = {r for r, nd in pairs if r and not nd}
        songs = {nd for r, nd in pairs if nd and not r}
        self._songs.update({r: None for r in refs})
        self._refs.update({nd: None for nd in songs})
        # Prefer the newest local row, just like the single-target path.
        dz = {r[3:] for r in refs if r.startswith("dz:")}
        for tr in (
            await self.s.execute(
                select(Track)
                .where(or_(Track.nd_song_id.in_(songs), Track.deezer_track_id.in_(dz)), Track.deleted_at.is_(None))
                .order_by(Track.id.desc())
            )
        ).scalars():
            ref = f"dz:{tr.deezer_track_id}" if tr.deezer_track_id else None
            if ref in refs and tr.nd_song_id and not self._songs[ref]:
                self._songs[ref] = tr.nd_song_id
            if tr.nd_song_id in songs and ref and not self._refs[tr.nd_song_id]:
                self._refs[tr.nd_song_id] = ref
        for model, column in (
            (DownloadItem, DownloadItem.query_json["meta"]["track_ref"].astext),
            (ImportItem, ImportItem.ref),
        ):
            rows = (
                await self.s.execute(
                    select(column, Track.nd_song_id)
                    .join(Track, model.track_id == Track.id)
                    .where(
                        or_(column.in_(refs), Track.nd_song_id.in_(songs)),
                        Track.nd_song_id.is_not(None),
                        Track.deleted_at.is_(None),
                    )
                    .order_by(model.id.desc())
                )
            ).all()
            for ref, nd in rows:
                if ref in refs and not self._songs[ref]:
                    self._songs[ref] = nd
                if nd in songs and ref and not self._refs[nd]:
                    self._refs[nd] = ref

    async def complete(self, ref: str | None, nd_song_id: str | None) -> tuple[str | None, str | None]:
        if ref and not nd_song_id:
            if ref not in self._songs:
                self._songs[ref] = await _song_of_ref(self.s, ref)
            nd_song_id = self._songs[ref]
        elif nd_song_id and not ref:
            if nd_song_id not in self._refs:
                self._refs[nd_song_id] = await _ref_of_song(self.s, nd_song_id)
            ref = self._refs[nd_song_id]
        return ref, nd_song_id


# --- events --------------------------------------------------------------------------------------------------


def event_time(ts: datetime, now: datetime | None = None) -> datetime:
    """Timezone-aware UTC (a naive client timestamp is read as UTC); an obviously wrong clock is corrected,
    with a three-calendar-year intake window."""
    now = now or utcnow()
    ts = ts.astimezone(UTC) if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    try:
        cutoff = now.replace(year=now.year - 3)
    except ValueError:  # February 29 in a non-leap target year
        cutoff = now.replace(year=now.year - 3, day=28)
    return now if ts > now + MAX_FUTURE or ts < cutoff else ts


def dislike_source(client: str) -> str:
    return "app" if client.startswith("app") else client[:16]


@dataclass
class Recorded:
    accepted: int = 0
    duplicates: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)


async def _track_length(s: AsyncSession, nd_song_id: str, duration_s: float | None) -> timedelta:
    if not duration_s:
        duration_s = (
            await s.execute(
                select(Track.duration_s).where(Track.nd_song_id == nd_song_id, Track.deleted_at.is_(None)).limit(1)
            )
        ).scalar_one_or_none()
    return timedelta(seconds=min(float(duration_s or 0), 3600))


async def _drop_scrobbles_near(s: AsyncSession, user_id: int, ev: PlayEvent) -> None:
    """The app's own measured event of a track replaces a client scrobble of that same listen (by any identifier)."""
    conds = _same_track(ev)
    if conds is None:
        return
    length = await _track_length(s, ev.nd_song_id, ev.duration_s) if ev.nd_song_id else _duration(ev.duration_s)
    w = DEDUP_WINDOW + length
    await s.execute(
        update(PlayEvent)
        .where(
            PlayEvent.user_id == user_id,
            PlayEvent.scrobble.is_(True),
            PlayEvent.kind == "finish",
            PlayEvent.ts.between(ev.ts - w, ev.ts + w),
            or_(*conds),
        )
        .values(superseded_scrobble=True)
    )


async def _track_duration(s: AsyncSession, uid: str | None, nd_song_id: str | None) -> float | None:
    if uid:
        found = (
            await s.execute(select(Track.duration_s).where(Track.uid == uid, Track.deleted_at.is_(None)).limit(1))
        ).scalar_one_or_none()
    else:
        found = (
            await s.execute(
                select(Track.duration_s).where(Track.nd_song_id == nd_song_id, Track.deleted_at.is_(None)).limit(1)
            )
        ).scalar_one_or_none()
    return float(found) if found else None


async def _is_counted_listen(
    s: AsyncSession, uid: str | None, nd: str | None, listened_ms: int | None, duration_s: float | None
) -> bool:
    """The summary counts a measured listen when cumulative `listened_ms` crosses its threshold; only that wins."""
    if listened_ms is None:
        return False
    from app.listening_summary import listened_threshold_ms

    duration = duration_s or await _track_duration(s, uid, nd)
    return listened_ms >= listened_threshold_ms(duration)


def _duration(duration_s: float | None) -> timedelta:
    return timedelta(seconds=min(float(duration_s or 0), 3600))


def _same_track(ev: PlayEvent) -> list | None:
    """Predicates naming the same library track: the Hub uid first, then the deprecated aliases."""
    conds = []
    if ev.track_uid:
        conds.append(PlayEvent.track_uid == ev.track_uid)
    if ev.nd_song_id:
        conds.append(PlayEvent.nd_song_id == ev.nd_song_id)
    if ev.ref:
        conds.append(PlayEvent.ref == ev.ref)
    return conds or None


async def _listening_metadata(s: AsyncSession, e: PlayEventIn, nd: str | None) -> dict[str, Any] | None:
    """Snapshot known facts so later removal of a catalog cache entry or library file cannot erase them."""
    data: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    if e.track_metadata:
        data = e.track_metadata.model_dump(exclude_none=True)
        provenance = {key: "client" for key in data}
    if nd:
        track = (
            await s.execute(
                select(Track)
                .where(Track.nd_song_id == nd, Track.deleted_at.is_(None))
                .order_by(Track.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if track:
            for key, value in {
                "title": track.title,
                "album": track.album,
                "artist_credit": track.artist,
                "duration_s": track.duration_s,
                "isrc": track.isrc,
                "year": track.year,
                "genre": track.genre,
            }.items():
                if value is not None and not data.get(key):
                    data[key] = value
                    provenance[key] = "library"
    if e.ref:
        cache_keys = [f"mix:track:{e.ref}"]
        if e.ref.startswith("dz:"):
            cache_keys.append(f"dz:track:{e.ref[3:]}")
        for key in cache_keys:
            cached = await s.get(CatalogCache, key)
            raw = cached.payload_json if cached else None
            if not isinstance(raw, dict) or raw.get("ref") != e.ref:
                continue
            values = {
                "title": raw.get("title"),
                "album": raw.get("album_title"),
                "artist_credit": raw.get("artist"),
                "duration_s": raw.get("duration_s"),
                "isrc": raw.get("isrc"),
            }
            release = raw.get("release_date")
            if isinstance(release, str) and len(release) >= 4 and release[:4].isdigit():
                values["year"] = int(release[:4])
            names = raw.get("artists")
            if isinstance(names, list) and all(isinstance(x, str) and x for x in names):
                values["artists"] = [
                    {
                        "name": name,
                        **(
                            {"ref": raw["artist_ref"]}
                            if index == 0 and raw.get("artist_ref") and name == raw.get("artist")
                            else {}
                        ),
                    }
                    for index, name in enumerate(names)
                ]
            for name, value in values.items():
                if name == "artists" and value and data.get(name):
                    existing = data[name]
                    old_names = [artist.get("name") for artist in existing]
                    new_names = [artist.get("name") for artist in value]
                    changed = False
                    for artist in value:
                        artist_name = artist.get("name")
                        if old_names.count(artist_name) == 1 and new_names.count(artist_name) == 1:
                            match = existing[old_names.index(artist_name)]
                            if artist.get("ref") and not match.get("ref"):
                                match["ref"] = artist["ref"]
                                changed = True
                        elif artist_name not in old_names:
                            existing.append(artist.copy())
                            changed = True
                    if changed:
                        provenance[name] = "+".join(sorted({provenance.get(name, "client"), "catalog_cache"}))
                elif value and not data.get(name):
                    data[name] = value
                    provenance[name] = "catalog_cache"
    if not data:
        return None
    data["provenance"] = provenance
    return data


async def record_events(
    session: AsyncSession, user_id: int, raw: Iterable[Any], now: datetime | None = None
) -> Recorded:
    """Stores a batch of events (`PlayEventIn` each). A malformed event is rejected alone, so a client can drop the
    whole batch it sent; an `event_id` seen before is a duplicate."""
    now = now or utcnow()
    out = Recorded()
    events: list[PlayEventIn] = []
    for i, item in enumerate(raw):
        try:
            events.append(PlayEventIn.model_validate(item))
        except ValidationError as e:
            out.rejected.append(
                {
                    "index": i,
                    "event_id": item.get("event_id") if isinstance(item, dict) else None,
                    "detail": "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()),
                }
            )
    ids = {e.event_id for e in events}
    seen = set(
        (
            await session.execute(
                select(PlayEvent.event_id).where(PlayEvent.user_id == user_id, PlayEvent.event_id.in_(ids))
            )
        ).scalars()
    )
    # Causal retractions can restore an old decision after its mirror was already
    # delivered. Requeue the resulting state, not the causal event'session kind.
    causal_batch = any(e.reverts_event_id for e in events)
    if not causal_batch and ids:
        causal_batch = (
            await session.execute(
                select(PlayEvent.id).where(PlayEvent.user_id == user_id, PlayEvent.reverts_event_id.in_(ids)).limit(1)
            )
        ).first() is not None
    before_favorites: dict[Any, Any] = {}
    if causal_batch:
        before_favorites = await _favorites_snapshot(session, user_id, now)
    fill = Ids(session)
    for e in events:
        if e.event_id in seen:
            out.duplicates += 1
            continue
        seen.add(e.event_id)
        ref, nd = await fill.complete(e.ref, e.nd_song_id) if e.target_type == "track" else (e.ref, None)
        metadata = (
            await _listening_metadata(session, e, nd)
            if e.kind in ("start", "progress", "interrupted", "error", "skip", "finish")
            else None
        )
        uid = e.uid if e.target_type == "track" else None
        if e.target_type == "track" and not uid:
            uid = await track_uid_of(session, ref, nd)
        values = dict(
            user_id=user_id,
            event_id=e.event_id,
            ts=event_time(e.ts, now),
            kind=e.kind,
            ref=ref,
            track_uid=uid,
            nd_song_id=nd,
            position_s=e.position_s,
            duration_s=e.duration_s,
            context=e.context,
            client=e.client,
            received_at=now,
            playback_id=e.playback_id,
            device_id=e.device_id,
            listened_ms=e.listened_ms,
            track_metadata=metadata,
            target_type=e.target_type,
            nd_album_id=e.nd_album_id,
            nd_artist_id=e.nd_artist_id,
            target_data=e.target_data,
            reverts_event_id=e.reverts_event_id,
            launch_id=e.launch_id,
            launch_origin=e.launch_origin,
        )
        res = await session.execute(pg_insert(PlayEvent).values(**values).on_conflict_do_nothing())
        if not _rowcount(res):  # a concurrent request stored it first
            out.duplicates += 1
            continue
        out.accepted += 1
        if e.kind in ("like", "unlike"):
            from app.api.schemas import FavoriteTarget
            from app.favorites import remember

            await remember(
                session,
                FavoriteTarget(
                    target_type=e.target_type,
                    ref=ref,
                    nd_song_id=nd,
                    nd_album_id=e.nd_album_id,
                    nd_artist_id=e.nd_artist_id,
                    target_data=e.target_data,
                ),
            )
        if (
            e.kind in PLAYBACK_KINDS
            and e.client != "navidrome"
            and (uid or ref or nd)
            and await _is_counted_listen(session, uid, nd, e.listened_ms, e.duration_s)
        ):
            await _drop_scrobbles_near(session, user_id, PlayEvent(**values))
        if e.kind == "dislike":
            await add_dislike(
                session,
                user_id,
                ref=ref if e.target_type == "track" else None,
                nd_song_id=nd,
                artist_ref=(ref or f"nd:{e.nd_artist_id}") if e.target_type == "artist" else None,
                artist=(e.target_data or {}).get("name") if e.target_type == "artist" else None,
                source=dislike_source(e.client),
                complete=False,
            )
        elif e.kind == "undislike":
            await remove_dislikes(
                session,
                user_id,
                ref=ref if e.target_type == "track" else None,
                nd_song_id=nd,
                artist_ref=(ref or f"nd:{e.nd_artist_id}") if e.target_type == "artist" else None,
                complete=False,
            )
    if causal_batch and out.accepted:
        after_favorites = await _favorites_snapshot(session, user_id, now)
        for key, state in after_favorites.items():
            previous = before_favorites.get(key)
            if previous is None or previous.liked != state.liked:
                if state.target_type == "track" and state.ref and not state.liked:
                    from app.favorites import clear_pending

                    await clear_pending(session, user_id, state.ref)
        for key, previous in before_favorites.items():
            if key not in after_favorites and previous.liked and previous.target_type == "track" and previous.ref:
                from app.favorites import clear_pending

                await clear_pending(session, user_id, previous.ref)
    return out


async def record_scrobble(
    session: AsyncSession, user_id: int, e: ScrobbleIn, *, now: datetime | None = None
) -> tuple[PlayEvent, bool]:
    """Store a client scrobble; returns (event, duplicate). A scrobble near a measured app event is superseded."""
    now = now or utcnow()
    existing = (
        await session.execute(select(PlayEvent).where(PlayEvent.user_id == user_id, PlayEvent.event_id == e.event_id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, True  # a resent scrobble: keep the stored verdict (`counted`) truthful
    ref, nd = await Ids(session).complete(e.ref, e.nd_song_id)
    uid = e.track_uid or await track_uid_of(session, ref, nd)
    if not (uid or ref or nd):
        raise ValueError("scrobble names no track")
    ts = event_time(e.ts, now)
    length = (
        await _track_length(session, nd, e.duration_s) if nd else timedelta(seconds=min(float(e.duration_s or 0), 3600))
    )
    conds = []
    if uid:
        conds.append(PlayEvent.track_uid == uid)
    if nd:
        conds.append(PlayEvent.nd_song_id == nd)
    if ref:
        conds.append(PlayEvent.ref == ref)
    w = DEDUP_WINDOW + length
    rows = list(
        (
            await session.execute(
                select(PlayEvent).where(
                    PlayEvent.user_id == user_id,
                    PlayEvent.kind.in_(PLAYBACK_KINDS),
                    or_(*conds),
                    PlayEvent.ts.between(ts - w, ts + w),
                )
            )
        ).scalars()
    )
    measured = False
    for row in rows:
        if row.scrobble or row.client == "navidrome":
            continue
        if await _is_counted_listen(session, row.track_uid, row.nd_song_id, row.listened_ms, row.duration_s):
            measured = True
            break
    twin = any(row.scrobble and not row.superseded_scrobble for row in rows)
    metadata = await _listening_metadata(
        session,
        PlayEventIn(
            event_id=e.event_id,
            ts=ts,
            kind="finish",
            ref=ref,
            uid=uid,
            nd_song_id=nd,
            client=e.client,
            track_metadata=e.track_metadata,
        ),
        nd,
    )
    values = dict(
        user_id=user_id,
        event_id=e.event_id,
        ts=ts,
        kind="finish",
        ref=ref,
        track_uid=uid,
        nd_song_id=nd,
        duration_s=e.duration_s or (length.total_seconds() or None),
        position_s=e.position_s,
        context=e.context,
        client=e.client,
        received_at=now,
        track_metadata=metadata,
        scrobble=True,
        origin="client",
        superseded_scrobble=measured or twin,
    )
    res = await session.execute(pg_insert(PlayEvent).values(**values).on_conflict_do_nothing())
    stored = (
        await session.execute(select(PlayEvent).where(PlayEvent.user_id == user_id, PlayEvent.event_id == e.event_id))
    ).scalar_one()
    return stored, not _rowcount(res)


async def user_events(
    session: AsyncSession,
    user_id: int,
    days: float = 30,
    *,
    kinds: Iterable[str] | None = None,
    now: datetime | None = None,
) -> list[PlayEvent]:
    """The user'session events of the last `days`, oldest first (the raw material of the taste profile, MIXES.md 5)."""
    now = now or utcnow()
    q = select(PlayEvent).where(
        PlayEvent.user_id == user_id,
        PlayEvent.superseded_scrobble.is_(False),
        PlayEvent.ts >= now - timedelta(days=days),
        PlayEvent.ts <= now,
    )
    if kinds is not None:
        q = q.where(PlayEvent.kind.in_(list(kinds)))
    return list((await session.execute(q.order_by(PlayEvent.ts, PlayEvent.id))).scalars())


async def imported_likes(s: AsyncSession, user_id: int) -> set[str]:
    """Navidrome songs whose ♥ an import of another service set (`imports.options_json.liked`) and the user did not
    like again themselves since (a `like` event): such ♥ don't count for the taste profile (M4)."""
    liked: set[str] = set()
    for opts in (await s.execute(select(Import.options_json).where(Import.user_id == user_id))).scalars():
        liked.update((opts or {}).get("liked") or [])
    if not liked:
        return liked
    own = set(
        (
            await s.execute(
                select(PlayEvent.nd_song_id).where(
                    PlayEvent.user_id == user_id, PlayEvent.kind == "like", PlayEvent.nd_song_id.in_(liked)
                )
            )
        ).scalars()
    )
    return liked - own


# --- «Не нравится» ---------------------------------------------------------------------------------------------


def _target(ref: str | None, nd_song_id: str | None, artist_ref: str | None):
    if artist_ref:
        return Dislike.artist_ref == artist_ref
    conds = [
        c
        for c in (Dislike.ref == ref if ref else None, Dislike.nd_song_id == nd_song_id if nd_song_id else None)
        if c is not None
    ]
    return or_(*conds) if conds else None


async def add_dislike(
    s: AsyncSession,
    user_id: int,
    *,
    ref: str | None = None,
    nd_song_id: str | None = None,
    artist_ref: str | None = None,
    artist: str | None = None,
    title: str | None = None,
    source: str,
    complete: bool = True,
) -> Dislike | None:
    """«Не нравится» a track (by `ref` and/or `nd_song_id`) or an artist (`artist_ref`); the same target twice is
    one row (the ids it lacked are filled in). None: nothing to name."""
    if not artist_ref and complete:
        ref, nd_song_id = await Ids(s).complete(ref, nd_song_id)
    cond = _target(ref, nd_song_id, artist_ref)
    if cond is None:
        return None
    row = (await s.execute(select(Dislike).where(Dislike.user_id == user_id, cond).limit(1))).scalar_one_or_none()
    if row is None:
        row = Dislike(user_id=user_id, artist_ref=artist_ref, source=source, created_at=utcnow())
        s.add(row)
    if not artist_ref:
        row.ref, row.nd_song_id = row.ref or ref, row.nd_song_id or nd_song_id
    row.artist, row.title = row.artist or artist, row.title or title
    await s.flush()
    return row


async def remove_dislikes(
    s: AsyncSession,
    user_id: int,
    *,
    ref: str | None = None,
    nd_song_id: str | None = None,
    artist_ref: str | None = None,
    complete: bool = True,
) -> int:
    if not artist_ref and complete:
        ref, nd_song_id = await Ids(s).complete(ref, nd_song_id)
    cond = _target(ref, nd_song_id, artist_ref)
    if cond is None:
        return 0
    res = await s.execute(delete(Dislike).where(Dislike.user_id == user_id, cond))
    return _rowcount(res)


async def dislike_event(
    session: AsyncSession,
    user_id: int,
    kind: str,
    *,
    ref: str | None,
    nd_song_id: str | None,
    client: str,
    context: str | None = None,
    target_type: str = "track",
) -> None:
    """The `dislike`/`undislike` event of a track marked through `/api/dislikes` (the history keeps it too)."""
    await session.execute(
        pg_insert(PlayEvent).values(
            user_id=user_id,
            event_id=f"hub:{uuid.uuid4().hex}",
            ts=utcnow(),
            kind=kind,
            ref=ref,
            nd_song_id=nd_song_id,
            context=context,
            client=client,
            received_at=utcnow(),
            target_type=target_type,
        )
    )
