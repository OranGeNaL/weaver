"""Typed favorite decisions, causal rollback and verified aliases.

The journal is the only source of own state: the Hub is the single owner of ♥ since stage 5. Imported stars remain
display-only provenance. No scoring window is applied when folding state; consumers decide how old a contribution may
be.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import FavoriteTarget
from app.models import (
    DownloadItem,
    FavoriteSubject,
    Import,
    ImportCollection,
    ImportItem,
    PlayEvent,
    Track,
    User,
    utcnow,
)

# the three kinds of a ♥ (FavoriteTarget.target_type); the database keeps them as plain text
TargetType = Literal["track", "album", "artist"]

log = None
TARGET_TYPES = ("track", "album", "artist")


def decision_id(operation: str, target_type: str, key: str) -> str:
    return "fav:" + hashlib.sha256(f"{operation}\0{target_type}\0{key}".encode()).hexdigest()[:48]


def subject_data(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Keep the shared directory small even after many different client snapshots."""
    raw = raw or {}
    data: dict[str, Any] = {
        k: str(raw[k])[:512]
        for k in ("name", "title", "artist", "artist_ref", "album_ref", "album_title", "cover_url", "cover_small_url")
        if raw.get(k)
    }
    for k in ("duration", "duration_s"):
        if isinstance(raw.get(k), (int, float)):
            data[k] = raw[k]
    if isinstance(raw.get("track_refs"), list):
        data["track_refs"] = [x[:64] for x in raw["track_refs"][:100] if isinstance(x, str)]
    return data


def aliases(obj: Any) -> set[str]:
    """The Hub uid and the catalog ref of one target, as alias keys."""
    uid = getattr(obj, "uid", None) or getattr(obj, "track_uid", None)
    return {x for x in (f"ref:{obj.ref}" if obj.ref else None, f"uid:{uid}" if uid else None) if x}


class AliasIndex:
    """Union of explicit ref/ND pairs and persisted verified aliases, isolated by type.

    `subjects` are the ORM rows or the taste snapshot's light ones: only `target_type` and `aliases` are read.
    """

    # subjects: the ORM `FavoriteSubject` rows or the taste snapshot's light `Subject` ones (same five fields);
    # a structural Protocol cannot match SQLAlchemy's `Mapped[...]` declarations under Pyright
    def __init__(self, events: Iterable[Any] = (), subjects: Iterable[Any] = ()):
        self.parent: dict[tuple[str, str], tuple[str, str]] = {}
        for subject in subjects:
            self.join(subject.target_type, set(subject.aliases or []))
        for ev in events:
            self.join(ev.target_type or "track", aliases(ev))

    def root(self, key: tuple[str, str]) -> tuple[str, str]:
        p = self.parent.setdefault(key, key)
        if p != key:
            self.parent[key] = self.root(p)
        return self.parent[key]

    def join(self, typ: str, keys: set[str]):
        roots = sorted({self.root((typ, k)) for k in keys})
        if roots:
            for r in roots[1:]:
                self.parent[r] = roots[0]

    def key(self, obj: Any) -> tuple[str, str]:
        return self.root((obj.target_type or "track", min(aliases(obj))))


@dataclass
class State:
    target_type: str
    key: str
    liked: bool
    since: datetime | None
    event: PlayEvent  # latest effective independent decision
    received_at: datetime  # latest arrival including causal compensation
    ref: str | None = None
    data: dict[str, Any] | None = None
    aliases: list[str] = field(default_factory=list)
    uid: str | None = None


def causal_activity(rows: list[PlayEvent], index: AliasIndex):
    """Resolve a functional causal graph iteratively, including untrusted cycles/long chains.

    Only chains ending at a same-user, same-subject independent decision have an effect.
    A node is active iff none of its active retractors remain; parallel retractors are OR.
    """
    from collections import deque

    by_id = {(e.user_id, e.event_id): e for e in rows}
    parents: dict[tuple[int, str], tuple[int, str]] = {}
    for key, e in by_id.items():
        if not e.reverts_event_id:
            continue
        target = (e.user_id, e.reverts_event_id)
        if target in by_id and index.key(e) == index.key(by_id[target]):
            parents[key] = target
    roots = {}
    for start in by_id:
        path, visiting = [], set()
        key = start
        while key not in roots and key not in visiting:
            visiting.add(key)
            path.append(key)
            e = by_id[key]
            if not e.reverts_event_id:
                roots[key] = key
                break
            if key not in parents:
                roots[key] = None
                break
            key = parents[key]
        root = roots.get(key)  # A cycle (and every chain into it) is inert.
        for key in path:
            roots[key] = root
    remaining = {key: 0 for key, root in roots.items() if root is not None}
    for child, parent in parents.items():
        if child in remaining:
            remaining[parent] += 1
    queue = deque(key for key, count in remaining.items() if not count)
    active, blocked = set(), set()
    while queue:
        key = queue.popleft()
        if key not in blocked:
            active.add(key)
        if key in parents:
            parent = parents[key]
            if key in active:
                blocked.add(parent)
            remaining[parent] -= 1
            if not remaining[parent]:
                queue.append(parent)
    return active, roots


def _aware(value: datetime) -> datetime:
    """A naive value is UTC (the schema stores timestamptz; old rows and test fixtures may be naive)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _decision_inputs(events, subjects, now):
    """Shared causal/redundancy resolution for current state and activity consumers."""
    cutoff = _aware(now) if now is not None else None
    rows = list(
        {
            (e.user_id, e.event_id): e
            for e in events
            if e.kind in ("like", "unlike") and (cutoff is None or _aware(e.ts) <= cutoff)
        }.values()
    )
    index = AliasIndex(rows, subjects)
    active, roots = causal_activity(rows, index)

    def identity(e):
        return e.user_id, e.event_id

    ordered = sorted(rows, key=lambda e: (e.ts, e.received_at, e.id or 0, e.event_id))
    last_kind: dict[tuple[int, tuple[str, str]], str] = {}
    series: dict[tuple[int, tuple[str, str]], tuple[int, str]] = {}
    redundant = set()
    for e in ordered:
        if e.reverts_event_id:
            key = (e.user_id, index.key(e))
            if roots[identity(e)] == (e.user_id, e.reverts_event_id) and series.get(key) == (
                e.user_id,
                e.reverts_event_id,
            ):
                last_kind.pop(key, None)
                series.pop(key, None)
            continue
        key = (e.user_id, index.key(e))
        if last_kind.get(key) == e.kind:
            redundant.add(identity(e))
        else:
            series[key] = identity(e)
        last_kind[key] = e.kind
    return rows, index, active, roots, ordered, redundant


def effective_decisions(
    events: Iterable[PlayEvent], subjects: Iterable[Any] = (), now: datetime | None = None
) -> list[PlayEvent]:
    """Effective independent rows only; causal nodes and legacy redundant retries are not actions."""
    _rows, _index, active, _roots, ordered, redundant = _decision_inputs(events, list(subjects), now)
    return [
        e
        for e in ordered
        if not e.reverts_event_id and (e.user_id, e.event_id) in active and (e.user_id, e.event_id) not in redundant
    ]


def fold(
    events: Iterable[PlayEvent], subjects: Iterable[Any] = (), now: datetime | None = None
) -> dict[tuple[str, str], State]:
    """Pure fold. Rollbacks remove their exact decision, even if delivered before it.

    Repeated states don't renew `since`. A rollback never becomes an independent opposite decision.
    Its target is checked as well as its id, so an unrelated object cannot be rolled back.
    """
    subjects = list(subjects)
    rows, index, active, roots, ordered, redundant = _decision_inputs(events, subjects, now)

    def identity(e):
        return e.user_id, e.event_id

    arrivals: dict[tuple[str, str], datetime] = {}
    for e in rows:
        if identity(e) in redundant or (
            e.reverts_event_id and (roots[identity(e)] is None or roots[identity(e)] in redundant)
        ):
            continue
        k = index.key(e)
        stamp: datetime = e.received_at or e.ts  # a row without an arrival time sorts by when it happened
        arrivals[k] = max(arrivals.get(k, stamp), stamp)
    out: dict[tuple[str, str], State] = {}
    for e in ordered:
        if e.reverts_event_id or identity(e) not in active or identity(e) in redundant:
            continue
        k = index.key(e)
        prev = out.get(k)
        liked = e.kind == "like"
        since = e.ts if liked else None
        out[k] = State(
            *k,
            liked,
            since,
            e,
            arrivals[k],
            e.ref or (prev.ref if prev else None),
            {**(prev.data or {} if prev else {}), **(e.target_data or {})},
            uid=getattr(e, "track_uid", None) or (prev.uid if prev else None),
        )
    # A rolled-back first like still has a neutral state and must not bootstrap a new like.
    for e in reversed(ordered):
        k = index.key(e)
        if k not in out and not e.reverts_event_id and identity(e) not in redundant:
            out[k] = State(*k, False, None, e, arrivals[k], e.ref, e.target_data)
    for subject in subjects:
        k = index.root((subject.target_type, min(subject.aliases))) if subject.aliases else None
        if k in out:
            st = out[k]
            st.ref = subject.ref or st.ref
            st.uid = getattr(subject, "uid", None) or st.uid
            st.data = {**(subject.data or {}), **(st.data or {})}
    for key, st in out.items():
        st.aliases = sorted(
            alias for typ, alias in index.parent if typ == st.target_type and index.root((typ, alias)) == key
        )
    return out


async def snapshot(session: AsyncSession, user_id: int, now: datetime | None = None):
    now = now or utcnow()
    q = select(PlayEvent).where(PlayEvent.user_id == user_id, PlayEvent.kind.in_(("like", "unlike")))
    q = q.where(PlayEvent.ts <= now)
    events = list((await session.execute(q)).scalars())
    subjects = list((await session.execute(select(FavoriteSubject))).scalars())
    partial = [(e.ref, e.track_uid) for e in events if e.target_type == "track" and not (e.ref and e.track_uid)]
    if partial:
        from app.plays import Ids

        fill = Ids(session)
        await fill.prime(partial)

        for ref, uid in set(partial):
            ref, uid = await fill.complete(ref, uid)
            if ref and uid:
                subjects.append(
                    FavoriteSubject(
                        target_type="track",
                        ref=ref,
                        uid=uid,
                        aliases=[f"ref:{ref}", f"uid:{uid}"],
                        canonical_key=f"ref:{ref}",
                        data={},
                    )
                )
    return fold(events, subjects, now)


async def remember(s: AsyncSession, target: FavoriteTarget, *, extra_aliases: Iterable[str] = ()) -> FavoriteSubject:
    typ = target.target_type
    keys = aliases(target) | set(extra_aliases)
    # Usually one indexed hit; JSON aliases also retain replaced ids of a merged subject.
    subjects = list((await s.execute(select(FavoriteSubject).where(FavoriteSubject.target_type == typ))).scalars())
    matches = [x for x in subjects if keys.intersection(x.aliases or [])]
    data: dict[str, Any] = {}
    for x in matches:
        keys.update(x.aliases or [])
        data.update(x.data or {})
    data = subject_data({**data, **(target.target_data or {})})
    if matches:
        row = matches[0]
        for other in matches[1:]:
            row.ref, row.uid = row.ref or other.ref, row.uid or other.uid
            await s.delete(other)
    else:
        row = FavoriteSubject(target_type=typ, canonical_key=min(keys), aliases=[], data={})
        s.add(row)
    if target.ref and (not row.ref or target.ref.startswith("dz:") or row.ref.startswith("file:")):
        row.ref = target.ref
    # A snapshot can still contain an old replaced song: observing that alias alone
    # must not move the canonical library id back from the verified replacement.
    if target.uid:
        if not row.uid or target.ref or extra_aliases:
            row.uid = target.uid
    row.aliases, row.data, row.enriched_at = sorted(keys), data, utcnow()
    await s.flush()
    return row


async def record(
    s: AsyncSession,
    user_id: int,
    target: FavoriteTarget,
    liked: bool,
    *,
    operation: str | None = None,
    ts: datetime | None = None,
    client: str = "hub",
    context: str | None = None,
    reverts_event_id: str | None = None,
    normalize: bool = True,
    event_id: str | None = None,
) -> PlayEvent:
    from app.plays import Ids, event_time

    target = target.model_copy()
    if target.target_type == "track":
        target.ref, target.uid = await Ids(s).complete(target.ref, target.uid)
    row = await remember(s, target)
    eid = event_id or decision_id(operation or uuid.uuid4().hex, target.target_type, row.canonical_key)
    now = utcnow()
    await s.execute(
        insert(PlayEvent)
        .values(
            **target.model_dump(exclude={"uid"}),
            track_uid=target.uid if target.target_type == "track" else None,
            user_id=user_id,
            event_id=eid,
            ts=event_time(ts or now, now) if normalize else ts or now,
            kind="like" if liked else "unlike",
            client=client,
            context=context,
            received_at=now,
            reverts_event_id=reverts_event_id,
        )
        .on_conflict_do_nothing()
    )
    return (
        await s.execute(select(PlayEvent).where(PlayEvent.user_id == user_id, PlayEvent.event_id == eid))
    ).scalar_one()


def target_of(state: State) -> FavoriteTarget:
    return FavoriteTarget(
        target_type=cast(TargetType, state.target_type),
        ref=state.ref,
        uid=state.uid,
        target_data=state.data,
    )


async def command(
    s: AsyncSession,
    user: User,
    targets: list[FavoriteTarget],
    *,
    liked: bool,
    operation: str | None = None,
    ts: datetime | None = None,
    client: str = "pwa",
    context: str | None = None,
) -> list[PlayEvent]:
    """Journal the user's decisions; the Hub is their only owner, so no external service is called.

    A retried command is idempotent by `event_id`: it returns the stored event and never replays an old decision.
    """
    from app.plays import Ids, event_time

    operation = operation or uuid.uuid4().hex
    events: list[PlayEvent] = []
    for typ in TARGET_TYPES:
        group = [t.model_copy() for t in targets if t.target_type == typ]
        if not group:
            continue
        event_ids = {
            id(t): operation if len(targets) == 1 else decision_id(operation, typ, min(aliases(t))) for t in group
        }
        for t in group:
            if typ == "track":
                t.ref, t.uid = await Ids(s).complete(t.ref, t.uid)
        for target in group:
            subject = await remember(s, target)
            if not target.uid and subject.uid:
                target.uid = subject.uid
            eid = event_ids[id(target)]
            previous = (
                await s.execute(select(PlayEvent).where(PlayEvent.user_id == user.id, PlayEvent.event_id == eid))
            ).scalar_one_or_none()
            if previous:
                events.append(previous)
                continue  # a retried command leaves the newer decision alone
            at = event_time(ts or utcnow())
            events.append(
                await record(
                    s,
                    user.id,
                    target,
                    liked,
                    operation=operation,
                    ts=at,
                    client=client,
                    context=context,
                    event_id=eid,
                )
            )
            if not liked and typ == "track" and target.ref:
                await clear_pending(s, user.id, target.ref)
    return events


async def clear_pending(s: AsyncSession, user_id: int, ref: str):
    from app.imports.apply import unlike_refs
    from app.jobs.downloads import item_ref

    await unlike_refs(s, user_id, [ref])
    items = (await s.execute(select(DownloadItem).where(DownloadItem.user_id == user_id))).scalars()
    for item in items:
        if item_ref(item) != ref:
            continue
        q = dict(item.query_json or {})
        was_star_only = (
            q.get("star") and not q.get("requested") and not any(q.get(k) for k in ("import", "mix", "wave"))
        )
        q.update(star=False, star_cancelled=True)
        item.query_json = q
        if was_star_only and item.status == "queued":
            item.status = "cancelled"
    await s.flush()


async def download_requested(s: AsyncSession, item: DownloadItem):
    from app.jobs.downloads import item_ref

    ref = item_ref(item)
    if not ref:
        return
    event = await record(
        s,
        item.user_id,
        FavoriteTarget(ref=ref, target_data=item.query_json.get("query")),
        True,
        operation=f"download:{item.id}:{uuid.uuid4().hex}",
        client="assistant" if item.query_json.get("favorite_client") == "assistant" else "hub",
        context=item.query_json.get("favorite_context") or "download",
    )
    item.query_json = {
        **item.query_json,
        "star_event_id": event.event_id,
        "star_cancelled": False,
        "star_failed": False,
    }


async def download_failed(s: AsyncSession, item: DownloadItem):
    q = item.query_json or {}
    if not q.get("star") or q.get("star_cancelled") or not q.get("star_event_id"):
        return
    original = (
        await s.execute(
            select(PlayEvent).where(PlayEvent.user_id == item.user_id, PlayEvent.event_id == q["star_event_id"])
        )
    ).scalar_one_or_none()
    if original:
        await record(
            s,
            item.user_id,
            FavoriteTarget(ref=original.ref, uid=original.track_uid),
            False,
            operation=f"failed:{original.event_id}",
            reverts_event_id=original.event_id,
            client="hub",
            context="download:failed",
        )
        item.query_json = {**q, "star_failed": True}


async def imported_targets(s: AsyncSession, user_id: int) -> set[tuple[str, str]]:
    """All known import provenance, including old album/artist imports conservatively matched by catalog ref."""
    keys = set()
    imports = list((await s.execute(select(Import).where(Import.user_id == user_id))).scalars())
    for imp in imports:
        opts = imp.options_json or {}
        for typ, opt in (("album", "liked_album_ids"), ("artist", "liked_artist_ids")):
            for x in opts.get(opt, []):
                keys.add((typ, f"uid:{x}"))
        for x in opts.get("liked_uids", []):
            keys.add(("track", f"uid:{x}"))
    rows = (
        await s.execute(
            select(ImportItem.ref, ImportItem.track_id, ImportCollection.kind)
            .join(ImportCollection, ImportCollection.id == ImportItem.collection_id)
            .where(
                ImportItem.import_id.in_([i.id for i in imports]),
                ImportCollection.selected.is_(True),
                ImportCollection.kind.in_(("likes", "albums", "artists")),
            )
        )
    ).all()
    kinds = {"likes": "track", "albums": "album", "artists": "artist"}
    for ref, track_id, kind in rows:
        typ = kinds[kind]
        if ref:
            keys.add((typ, f"ref:{ref}"))
        if track_id is not None and typ == "track":
            uid = (await s.execute(select(Track.uid).where(Track.id == track_id))).scalar_one_or_none()
            if uid:
                keys.add((typ, f"uid:{uid}"))
    return keys


def parse_time(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None
