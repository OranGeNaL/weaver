"""Pending playlist additions: manual review must not hold ready songs hostage (Hub playlists since stage 5)."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app import db
from app.jobs.downloads import process_playlist_adds
from app.models import DownloadBatch, DownloadItem, PendingPlaylistAdd, Playlist, PlaylistItem, Track, User
from app.security import encrypt


@pytest.fixture
async def playlist_env(hub_env, db_url: str):
    db.init_engine(db_url)
    async with db.session_scope() as s:
        user = User(username="qa", nd_username="qa", nd_password_enc=encrypt("qa-password"))
        s.add(user)
        await s.flush()
        existing = Track(
            uid=str(uuid.uuid4()),
            path="existing.mp3",
            artist="A",
            title="existing",
            fingerprint="existing",
            nd_song_id="existing",
        )
        pl = Playlist(uid=f"pl-{user.id}", owner_id=user.id, name="Плейлист")
        s.add_all([existing, pl])
        await s.flush()
        s.add(PlaylistItem(uid=str(uuid.uuid4()), playlist_id=pl.id, track_uid=existing.uid, position=1024))
        user_id, playlist_uid = user.id, pl.uid
    try:
        yield user_id, playlist_uid
    finally:
        await db.dispose()


async def seed(user_id, playlist_uid, statuses):
    async with db.session_scope() as s:
        batch = DownloadBatch(user_id=user_id, origin="assistant", target_playlist_id=playlist_uid)
        s.add(batch)
        await s.flush()
        ids = []
        for pos, status in enumerate(statuses):
            item = DownloadItem(
                batch_id=batch.id,
                user_id=user_id,
                status=status,
                query_json={"query": {"artist": "Artist", "title": f"Song {pos}"}},
            )
            s.add(item)
            await s.flush()
            ids.append(item.id)
            s.add(
                PendingPlaylistAdd(playlist_uid=playlist_uid, user_id=user_id, download_item_id=item.id, position=pos)
            )
            if status == "done":
                await finish(s, item)
        return ids


async def finish(s, item):
    track = Track(
        uid=str(uuid.uuid4()),
        path=f"{item.id}.mp3",
        artist="Artist",
        title=f"Song {item.id}",
        fingerprint=str(item.id),
        nd_song_id=f"song{item.id}",
    )
    s.add(track)
    await s.flush()
    item.track_id, item.status = track.id, "done"


async def sync():
    async with db.session_scope() as s:
        await process_playlist_adds(s)


async def pending():
    async with db.session_scope() as s:
        return list((await s.execute(select(PendingPlaylistAdd).order_by(PendingPlaylistAdd.id))).scalars())


async def songs(playlist_uid: str) -> list[str]:
    async with db.session_scope() as s:
        pl = (await s.execute(select(Playlist).where(Playlist.uid == playlist_uid))).scalar_one()
        rows = list(
            (
                await s.execute(
                    select(PlaylistItem, Track)
                    .join(Track, Track.uid == PlaylistItem.track_uid)
                    .where(PlaylistItem.playlist_id == pl.id)
                    .order_by(PlaylistItem.position, PlaylistItem.id)
                )
            ).all()
        )
        return [t.nd_song_id or t.uid for _item, t in rows]


async def test_review_releases_ready_songs_and_retry_inserts_in_requested_order(playlist_env, db_url: str):
    user_id, pid = playlist_env
    ids = await seed(user_id, pid, ["done", "needs_review", "done", "done"])
    await sync()
    assert await songs(pid) == ["existing", f"song{ids[0]}", f"song{ids[2]}", f"song{ids[3]}"]
    assert [p.download_item_id for p in await pending()] == [ids[1]]

    # Persisted anchors survive a restart; unrelated playlist edits survive the delayed insertion.
    await db.dispose()
    db.init_engine(db_url)
    async with db.session_scope() as s:
        await finish(s, await s.get(DownloadItem, ids[1]))
    await sync()
    assert await songs(pid) == ["existing", *(f"song{i}" for i in ids)]
    assert await pending() == []
    await sync()  # idempotent: a second pass adds nothing
    assert await songs(pid) == ["existing", *(f"song{i}" for i in ids)]


async def test_reviewed_first_item_returns_to_its_place(playlist_env):
    """A row waiting for review *before* an already inserted one must come back before it, not at the end."""
    user_id, pid = playlist_env
    ids = await seed(user_id, pid, ["needs_review", "done"])
    await sync()
    assert await songs(pid) == ["existing", f"song{ids[1]}"]
    async with db.session_scope() as s:
        await finish(s, await s.get(DownloadItem, ids[0]))
    await sync()
    assert await songs(pid) == ["existing", f"song{ids[0]}", f"song{ids[1]}"]
    assert await pending() == []


async def test_active_download_still_preserves_order(playlist_env):
    user_id, pid = playlist_env
    ids = await seed(user_id, pid, ["done", "downloading", "done"])
    await sync()
    assert await songs(pid) == ["existing", f"song{ids[0]}"]
    async with db.session_scope() as s:
        item = await s.get(DownloadItem, ids[1])
        assert item is not None
        item.status = "needs_review"
    await sync()
    assert await songs(pid) == ["existing", f"song{ids[0]}", f"song{ids[2]}"]


async def test_batches_keep_submission_order_and_cancel_releases_anchors(playlist_env):
    user_id, pid = playlist_env
    first = await seed(user_id, pid, ["needs_review", "done"])
    second = await seed(user_id, pid, ["done", "done"])
    await sync()
    assert await songs(pid) == ["existing", *(f"song{i}" for i in first[1:] + second)]
    async with db.session_scope() as s:
        item = await s.get(DownloadItem, first[0])
        assert item is not None
        item.status = "cancelled"
    await sync()
    assert await pending() == []


async def test_concurrent_completions_do_not_duplicate_playlist_songs(playlist_env):
    user_id, pid = playlist_env
    ids = await seed(user_id, pid, ["needs_review", "done", "done"])
    await asyncio.gather(sync(), sync())
    assert await songs(pid) == ["existing", *(f"song{i}" for i in ids[1:])]


async def test_delayed_insertion_preserves_a_large_playlist_in_one_request(playlist_env):
    user_id, pid = playlist_env
    ids = await seed(user_id, pid, ["needs_review", "done"])
    await sync()
    async with db.session_scope() as s:
        pl = (await s.execute(select(Playlist).where(Playlist.uid == pid))).scalar_one()
        manual = []
        for i in range(650):
            t = Track(
                uid=str(uuid.uuid4()),
                path=f"manual{i}.mp3",
                artist="A",
                title=f"manual{i}",
                fingerprint=f"manual{i}",
                nd_song_id=f"manual{i}",
            )
            s.add(t)
            manual.append(t)
        await s.flush()
        for i, t in enumerate(manual):
            s.add(PlaylistItem(uid=str(uuid.uuid4()), playlist_id=pl.id, track_uid=t.uid, position=4096 + i))
        await finish(s, await s.get(DownloadItem, ids[0]))
    await sync()
    # the reviewed first item comes back before the batch's next song; the manual tail is intact
    order = await songs(pid)
    assert order[:3] == ["existing", f"song{ids[0]}", f"song{ids[1]}"]
    assert [x for x in order if x.startswith("manual")] == [f"manual{i}" for i in range(650)]
