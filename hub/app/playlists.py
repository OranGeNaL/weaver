"""Hub playlists: the Hub is the only owner of playlists.

A playlist is an ordered list of items; duplicates are allowed. Each item has its own `uid` and a `numeric`
position, so a background add goes between two neighbours (its `anchor_uid`) without rewriting every row. When the
gap between the neighbours runs out, the positions are rebalanced. Every write locks the playlist row, so parallel
inserts never lose an element.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Playlist, PlaylistItem, Track, utcnow

STEP = Decimal(1024)
MIN_GAP = Decimal("0.000001")


def new_uid() -> str:
    return str(uuid.uuid4())


async def create(s: AsyncSession, owner_id: int, name: str, *, system: bool = False) -> Playlist:
    row = Playlist(uid=new_uid(), owner_id=owner_id, name=name.strip(), system=system)
    s.add(row)
    await s.flush()
    return row


async def list_for_user(s: AsyncSession, user_id: int) -> list[tuple[Playlist, int, int]]:
    """Playlists of the user (their own and their `system` ones) with item count and total duration, newest last."""
    rows = (
        await s.execute(
            select(
                Playlist,
                func.count(Track.id),
                func.coalesce(func.sum(Track.duration_s), 0),
            )
            .outerjoin(PlaylistItem, PlaylistItem.playlist_id == Playlist.id)
            # the same visible rows as GET /api/playlists/{uid}: a trashed track has no item any more
            .outerjoin(
                Track,
                (Track.uid == PlaylistItem.track_uid) & Track.deleted_at.is_(None),
            )
            .where(Playlist.owner_id == user_id)
            .group_by(Playlist.id)
            .order_by(Playlist.created_at, Playlist.id)
        )
    ).all()
    return [(p, int(count), int(duration or 0)) for p, count, duration in rows]


async def get_owned(s: AsyncSession, user_id: int, uid: str) -> Playlist | None:
    return (
        await s.execute(select(Playlist).where(Playlist.uid == uid, Playlist.owner_id == user_id))
    ).scalar_one_or_none()


async def get_by_uid(s: AsyncSession, uid: str) -> Playlist | None:
    return (await s.execute(select(Playlist).where(Playlist.uid == uid))).scalar_one_or_none()


async def lock(s: AsyncSession, playlist_id: int) -> None:
    """Serializes inserts/removals of one playlist (a row lock held until the transaction ends)."""
    await s.execute(select(Playlist.id).where(Playlist.id == playlist_id).with_for_update())


async def items(s: AsyncSession, playlist_id: int) -> list[PlaylistItem]:
    return list(
        (
            await s.execute(
                select(PlaylistItem)
                .where(PlaylistItem.playlist_id == playlist_id)
                .order_by(PlaylistItem.position, PlaylistItem.id)
            )
        ).scalars()
    )


async def visible_items(s: AsyncSession, playlist_id: int) -> list[PlaylistItem]:
    """The items the client sees and indexes: a track in the trash or gone has no position any more."""
    return list(
        (
            await s.execute(
                select(PlaylistItem)
                .join(Track, Track.uid == PlaylistItem.track_uid)
                .where(PlaylistItem.playlist_id == playlist_id, Track.deleted_at.is_(None))
                .order_by(PlaylistItem.position, PlaylistItem.id)
            )
        ).scalars()
    )


async def track_uids(s: AsyncSession, playlist_id: int) -> list[str]:
    return [i.track_uid for i in await visible_items(s, playlist_id)]


async def tracks(s: AsyncSession, playlist_id: int) -> list[Track]:
    """The playlist's visible tracks in order (the same rows `visible_items` counts)."""
    rows = await visible_items(s, playlist_id)
    uids = [i.track_uid for i in rows]
    found: dict[str, Track] = {}
    if uids:
        found = {t.uid: t for t in (await s.execute(select(Track).where(Track.uid.in_(uids)))).scalars()}
    return [found[i.track_uid] for i in rows if i.track_uid in found]


async def _rebalance(s: AsyncSession, rows: Iterable[PlaylistItem]) -> list[PlaylistItem]:
    ordered = sorted(rows, key=lambda i: (i.position, i.id or 0))
    for pos, item in enumerate(ordered):
        item.position = STEP * (pos + 1)
    await s.flush()
    return ordered


async def _position_after(s: AsyncSession, playlist_id: int, anchor_uid: str | None) -> Decimal:
    rows = await items(s, playlist_id)
    if not rows:
        return STEP
    if anchor_uid:
        index = next((n for n, i in enumerate(rows) if i.uid == anchor_uid), None)
        if index is not None:
            anchor = rows[index].position
            nxt = rows[index + 1].position if index + 1 < len(rows) else None
            if nxt is None:
                return anchor + STEP
            gap = nxt - anchor
            if gap <= MIN_GAP:
                rows = await _rebalance(s, rows)
                anchor = rows[index].position
                nxt = rows[index + 1].position
                gap = nxt - anchor
            return anchor + gap / 2
    return rows[-1].position + STEP


async def _position_before(s: AsyncSession, playlist_id: int, before_uid: str) -> Decimal | None:
    rows = await items(s, playlist_id)
    index = next((n for n, i in enumerate(rows) if i.uid == before_uid), None)
    if index is None:
        return None
    nxt = rows[index].position
    if index == 0:
        return nxt / 2 if nxt > MIN_GAP else None
    prev, gap = rows[index - 1].position, nxt - rows[index - 1].position
    if gap <= MIN_GAP:
        rows = await _rebalance(s, rows)
        prev, nxt = rows[index - 1].position, rows[index].position
        gap = nxt - prev
    return prev + gap / 2


async def add_tracks(
    s: AsyncSession,
    playlist: Playlist,
    track_uids: Iterable[str],
    *,
    anchor_uid: str | None = None,
    before_uid: str | None = None,
    added_by: int | None = None,
) -> list[PlaylistItem]:
    """Adds the tracks after `anchor_uid` or before `before_uid` (the end when both are gone); duplicates are kept."""
    uids = list(track_uids)
    if not uids:
        return []
    await lock(s, playlist.id)
    position = None
    if before_uid:
        position = await _position_before(s, playlist.id, before_uid)
    if position is None:
        position = await _position_after(s, playlist.id, anchor_uid)
    added = []
    for track_uid in uids:
        row = PlaylistItem(
            uid=new_uid(),
            playlist_id=playlist.id,
            track_uid=track_uid,
            position=position,
            anchor_uid=anchor_uid,
            added_by=added_by,
        )
        s.add(row)
        added.append(row)
        position += STEP
    playlist.updated_at = utcnow()
    await s.flush()
    return added


async def remove_indexes(s: AsyncSession, playlist: Playlist, indexes: Iterable[int]) -> int:
    """Removes items by their positions in the visible list (a track may be there twice)."""
    await lock(s, playlist.id)
    rows = await visible_items(s, playlist.id)
    gone = {n for n in indexes if 0 <= n < len(rows)}
    if not gone:
        return 0
    for n in sorted(gone, reverse=True):
        await s.delete(rows[n])
    playlist.updated_at = utcnow()
    await s.flush()
    return len(gone)


async def set_order(s: AsyncSession, playlist: Playlist, item_uids: list[str]) -> None:
    """Stores a new order: the named (visible) items first, in that order, then whatever was not named."""
    await lock(s, playlist.id)
    rows = await visible_items(s, playlist.id)
    by_uid = {i.uid: i for i in rows}
    ordered = [by_uid[u] for u in dict.fromkeys(item_uids) if u in by_uid]
    named = {i.uid for i in ordered}
    ordered += [i for i in rows if i.uid not in named]
    for pos, item in enumerate(ordered):
        item.position = STEP * (pos + 1)
    playlist.updated_at = utcnow()
    await s.flush()


async def replace_tracks(s: AsyncSession, playlist: Playlist, track_uids: list[str]) -> None:
    """Rewrites the whole list (a mix's daily songs); the playlist `uid` stays."""
    await lock(s, playlist.id)
    await s.execute(delete(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id))
    for pos, track_uid in enumerate(track_uids):
        s.add(
            PlaylistItem(
                uid=new_uid(),
                playlist_id=playlist.id,
                track_uid=track_uid,
                position=STEP * (pos + 1),
            )
        )
    playlist.updated_at = utcnow()
    await s.flush()


async def remove(s: AsyncSession, playlist: Playlist) -> None:
    await s.delete(playlist)
    await s.flush()
