"""The Navidrome library for the PWA in a browser (app/api/medialib.py, playlist pages): artists, albums,
«Понравившиеся», «Главная», covers.

Navidrome is a mock: artists «Кино» (♥, 2 albums) and «Кто-то» (1 album); album "alK" holds «Группа крови» (song
"ndK", known to the Hub as Deezer dz:100) and «Звезда» ("ndZ", only Navidrome knows it). Playlist "pl1" holds both.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from tests.support import app_client, grant_navidrome

OK = {"status": "ok", "version": "1.16.1"}
SONGS = [
    {
        "id": "ndK",
        "title": "Группа крови",
        "artist": "Кино",
        "artistId": "arK",
        "album": "Группа крови",
        "albumId": "alK",
        "coverArt": "al-alK",
        "duration": 245,
        "track": 1,
        "starred": "2026-10-01T10:00:00Z",
    },
    {
        "id": "ndZ",
        "title": "Звезда",
        "artist": "Кино",
        "artistId": "arK",
        "album": "Группа крови",
        "albumId": "alK",
        "coverArt": "al-alK",
        "duration": 220,
        "track": 2,
        "starred": "2026-10-20T10:00:00Z",
    },
]
ALBUMS = [
    {
        "id": "alK",
        "name": "Группа крови",
        "artist": "Кино",
        "artistId": "arK",
        "year": 1988,
        "songCount": 2,
        "duration": 465,
        "coverArt": "al-alK",
        "starred": "2026-10-02T00:00:00Z",
    },
    {
        "id": "alK2",
        "name": "Звезда по имени Солнце",
        "artist": "Кино",
        "artistId": "arK",
        "year": 1989,
        "songCount": 10,
    },
]
ARTISTS = [
    {"id": "arX", "name": "Кто-то", "albumCount": 1},
    {"id": "arK", "name": "Кино", "albumCount": 2, "coverArt": "ar-arK", "starred": "2026-10-01T00:00:00Z"},
]


@pytest.fixture
async def env(hub_env):
    from app.main import create_app

    # a test sets state["down"] to make Navidrome unreachable, state["fail"][<method>] = <Subsonic error code>
    state: dict = {"calls": [], "fail": {}, "down": False}

    def nd(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        p = request.url.params
        state["calls"].append((name, dict(p)))
        if state["down"]:
            raise httpx.ConnectError("connection refused", request=request)
        if name in state["fail"]:
            error = {"code": state["fail"][name], "message": "failed"}
            return httpx.Response(200, json={"subsonic-response": {"status": "failed", "error": error}})
        body: dict = {}
        if name == "getUser":
            body = {"user": {"adminRole": True}}
        elif name == "getArtists":
            body = {"artists": {"index": [{"name": "К", "artist": ARTISTS}]}}
        elif name == "getAlbumList2":
            body = {"albumList2": {"album": ALBUMS[:1] if p["type"] == "starred" else ALBUMS}}
        elif name == "getAlbum":
            if p["id"] != "alK":
                return httpx.Response(
                    200, json={"subsonic-response": {"status": "failed", "error": {"code": 70, "message": "not found"}}}
                )
            body = {"album": {**ALBUMS[0], "song": SONGS}}
        elif name == "getArtist":
            body = {"artist": {**ARTISTS[1], "album": state.get("artist_albums", ALBUMS)}}
        elif name == "getStarred2":
            body = {"starred2": {"song": SONGS, "album": ALBUMS[:1], "artist": ARTISTS[1:]}}
        elif name == "getPlaylist":
            body = {"playlist": {"id": "pl1", "name": "Дорога", "songCount": 2, "duration": 465, "entry": SONGS}}
        elif name == "getCoverArt":
            return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
        return httpx.Response(200, json={"subsonic-response": {**OK, **body}})

    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host="navidrome.test").mock(side_effect=nd)
            async with app_client(app) as client:
                await grant_navidrome("admin")  # stage-4 transition: the ND proxy tests need ND credentials
                await add_track("Кино", "Группа крови", "ndK", deezer_track_id="100")
                yield {"client": client, "state": state}


async def add_track(artist: str, title: str, nd_song_id: str, **kw) -> int:
    from app.db import session_scope
    from app.matching.normalize import fingerprint
    from app.models import Track

    async with session_scope() as s:
        t = Track(
            uid=str(uuid.uuid4()),
            path=f"{artist}/{title}.mp3",
            nd_song_id=nd_song_id,
            artist=artist,
            title=title,
            duration_s=245,
            fingerprint=fingerprint(artist, title),
            **kw,
        )
        s.add(t)
        await s.flush()
        return t.id


async def test_album_songs_are_owned_tracks_with_the_hubs_refs(env):
    r = await env["client"].get("/api/nd/albums/alK")
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["title"], a["year"], a["cover_url"]) == ("Группа крови", 1988, "/api/nd/cover/al-alK?size=300")
    k, z = a["tracks"]
    # the Hub's record gives the Deezer ref; a song only Navidrome knows plays by nd:<song>
    assert (k["ref"], k["nd_song_id"], k["owned"], k["library_track_id"] is not None) == ("dz:100", "ndK", True, True)
    assert (z["ref"], z["source"], z["nd_song_id"], z["library_track_id"]) == ("nd:ndZ", "nd", "ndZ", None)
    assert (z["nd_album_id"], z["nd_artist_id"], z["cover_url"]) == ("alK", "arK", "/api/nd/cover/al-alK?size=1000")

    assert (await env["client"].get("/api/nd/albums/nope")).status_code == 404


async def test_liked_lists_the_newest_like_first(env):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.api.schemas import FavoriteTarget
    from app.db import session_scope
    from app.favorites import command
    from app.models import User

    async with session_scope() as s:
        user = (await s.execute(select(User))).scalars().first()
        assert user is not None
        now = datetime.now(UTC)
        await command(s, user, [FavoriteTarget(ref="dz:100")], liked=True, operation="old", ts=now - timedelta(days=2))
        await command(
            s, user, [FavoriteTarget(nd_song_id="ndZ")], liked=True, operation="new", ts=now - timedelta(days=1)
        )

    d = (await env["client"].get("/api/nd/liked")).json()
    assert [t["nd_song_id"] for t in d["songs"]] == ["ndZ", "ndK"]
    # albums/artists are Hub objects: nothing was liked there, so the lists stay empty
    assert d["albums"] == [] and d["artists"] == []


async def test_catalog_like_is_listed_before_download_and_unlike_removes_it(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.events import bus
    from app.models import DownloadItem, User

    c = env["client"]
    async with session_scope() as s:
        uid = (await s.execute(select(User.id).where(User.username == "admin"))).scalar_one()
    queue = bus.subscribe(uid)
    try:
        target = {
            "ref": "dz:999",
            "target_data": {"title": "WHAT TO DO?", "artist": "Jackboys", "duration_s": 250, "cover_url": "/cover.jpg"},
        }
        r = await c.post("/api/star", json={"targets": [target], "event_id": "catalog-like"})
        assert r.status_code == 200, r.text
        assert '"event": "library"' in queue.get_nowait()
        liked = (await c.get("/api/nd/liked")).json()["songs"]
        assert liked[0]["ref"] == "dz:999"
        assert (liked[0]["title"], liked[0]["artist"], liked[0]["cover_url"]) == (
            "WHAT TO DO?",
            "Jackboys",
            "/cover.jpg",
        )
        assert not liked[0]["owned"] and liked[0]["nd_song_id"] is None
        async with session_scope() as s:
            assert not (await s.execute(select(DownloadItem))).first()
        r = await c.post("/api/star", json={"targets": [target], "unstar": True, "event_id": "catalog-unlike"})
        assert r.status_code == 200, r.text
        assert "dz:999" not in [t["ref"] for t in (await c.get("/api/nd/liked")).json()["songs"]]
    finally:
        bus.unsubscribe(uid, queue)


@pytest.mark.parametrize("cache_key", ["listen:dz:999", "mix:track:dz:999", "dz:track:999", None])
async def test_old_catalog_like_uses_stored_metadata_without_network(env, cache_key):
    from app.catalog.cache import cache_set

    if cache_key:
        await cache_set(cache_key, {"title": "WHAT TO DO?", "artist": "Jackboys", "duration_s": 250}, 3600)
    c = env["client"]
    assert (await c.post("/api/star", json={"targets": [{"ref": "dz:999"}]})).status_code == 200
    song = (await c.get("/api/nd/liked")).json()["songs"][0]
    assert song["ref"] == "dz:999"
    assert (song["title"], song["artist"]) == (("WHAT TO DO?", "Jackboys") if cache_key else ("dz:999", ""))


async def test_own_like_and_unlike_show_up_immediately(env):
    c = env["client"]
    target = {"ref": "dz:100"}
    assert (await c.post("/api/star", json={"targets": [target]})).status_code == 200
    assert [t["ref"] for t in (await c.get("/api/nd/liked")).json()["songs"]] == ["dz:100"]
    assert (await c.post("/api/star", json={"targets": [target], "unstar": True})).status_code == 200
    assert (await c.get("/api/nd/liked")).json()["songs"] == []


async def test_pending_import_likes_are_visible_only_to_their_user_and_own_unlike_wins(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Import, ImportCollection, ImportItem, User

    async with session_scope() as s:
        uid = (await s.execute(select(User.id).where(User.username == "admin"))).scalar_one()
        other = User(username="other", nd_username="other", nd_password_enc="unused")
        s.add(other)
        await s.flush()
        for user_id, ref in ((uid, "dz:998"), (other.id, "dz:997")):
            imp = Import(user_id=user_id, source="yandex", method="profile", status="done")
            s.add(imp)
            await s.flush()
            coll = ImportCollection(import_id=imp.id, kind="likes", external_id="likes", title="Likes")
            s.add(coll)
            await s.flush()
            s.add(
                ImportItem(
                    import_id=imp.id,
                    collection_id=coll.id,
                    external_id=ref,
                    ref=ref,
                    status="waiting",
                    raw_json={"title": "Imported song", "artist": "Artist"},
                )
            )
    c = env["client"]
    songs = (await c.get("/api/nd/liked")).json()["songs"]
    assert "dz:998" in [t["ref"] for t in songs] and "dz:997" not in [t["ref"] for t in songs]
    assert (await c.post("/api/star", json={"targets": [{"ref": "dz:998"}], "unstar": True})).status_code == 200
    assert "dz:998" not in [t["ref"] for t in (await c.get("/api/nd/liked")).json()["songs"]]


async def test_home_rows_and_radio_with_liked_artists_first(env):
    r = await env["client"].get("/api/nd/home")
    assert r.status_code == 200, r.text
    d = r.json()
    assert [a["id"] for a in d["recent"]] == ["alK", "alK2"] and [a["id"] for a in d["starred"]] == ["alK"]
    assert [(a["name"], a["starred"]) for a in d["radio"]] == [("Кино", True), ("Кто-то", False)]
    types = sorted(p["type"] for name, p in env["state"]["calls"] if name == "getAlbumList2")
    assert types == ["frequent", "newest", "recent", "starred"]


async def test_artists_artist_page_and_album_lists(env):
    c = env["client"]
    assert [a["id"] for a in (await c.get("/api/nd/artists")).json()] == ["arX", "arK"]
    page = (await c.get("/api/nd/artists/arK")).json()
    assert page["artist"]["name"] == "Кино" and [a["year"] for a in page["albums"]] == [1989, 1988]
    r = await c.get("/api/nd/albums", params={"type": "newest", "offset": 50, "size": 25})
    assert r.status_code == 200 and len(r.json()) == 2
    assert ("getAlbumList2", {"type": "newest", "size": "25", "offset": "50"}) in [
        (n, {k: p[k] for k in ("type", "size", "offset")}) for n, p in env["state"]["calls"] if n == "getAlbumList2"
    ]
    assert (await c.get("/api/nd/albums", params={"type": "bogus"})).status_code == 422


async def test_artist_albums_newest_year_first_then_without_a_year(env):
    env["state"]["artist_albums"] = [
        {"id": "a1", "name": "Без года"},
        {"id": "a2", "name": "Б", "year": 1988},
        {"id": "a3", "name": "а", "year": 1988},
        {"id": "a4", "name": "Новый", "year": 1990},
        {"id": "a5", "name": "Ещё без года", "year": 0},
    ]
    page = (await env["client"].get("/api/nd/artists/arK")).json()
    assert [a["id"] for a in page["albums"]] == ["a4", "a3", "a2", "a1", "a5"]


async def test_playlist_page_remove_and_delete(env):
    c = env["client"]
    await add_track("Кино", "Звезда", "ndZ")
    created = await c.post("/api/playlists", json={"name": "Дорога"})
    assert created.status_code == 200, created.text
    uid = created.json()["id"]
    added = await c.post(f"/api/playlists/{uid}/add", json={"song_ids": ["ndK", "ndZ"]})
    assert added.status_code == 200 and added.json()["added"] == 2

    p = (await c.get(f"/api/playlists/{uid}")).json()
    assert (p["name"], p["song_count"]) == ("Дорога", 2)
    assert [t["ref"] for t in p["tracks"]] == ["dz:100", "nd:ndZ"]

    assert (await c.post(f"/api/playlists/{uid}/remove", json={"indexes": [1]})).json()["removed"] == 1
    assert [t["ref"] for t in (await c.get(f"/api/playlists/{uid}")).json()["tracks"]] == ["dz:100"]
    assert (await c.post(f"/api/playlists/{uid}/remove", json={"indexes": []})).status_code == 422

    assert (await c.delete(f"/api/playlists/{uid}")).json() == {"ok": True}
    assert (await c.get(f"/api/playlists/{uid}")).status_code == 404


async def test_playlist_reorder_and_anchor(env):
    c = env["client"]
    await add_track("Кино", "Звезда", "ndZ")
    uid = (await c.post("/api/playlists", json={"name": "Дорога"})).json()["id"]
    added = (await c.post(f"/api/playlists/{uid}/add", json={"song_ids": ["ndK", "ndZ"]})).json()
    first, second = added["uids"]
    # the anchor puts the new track right after the named item, not at the end
    extra = (await c.post(f"/api/playlists/{uid}/add", json={"song_ids": ["ndK"], "anchor_uid": first})).json()
    third = extra["uids"][0]
    refs = [t["ref"] for t in (await c.get(f"/api/playlists/{uid}")).json()["tracks"]]
    assert refs == ["dz:100", "dz:100", "nd:ndZ"]
    assert (first, extra) != (second, None)
    # a new order: the named items first, in that order
    await c.post(f"/api/playlists/{uid}/reorder", json={"item_uids": [second, first, third]})
    page = (await c.get(f"/api/playlists/{uid}")).json()
    assert [t["ref"] for t in page["tracks"]] == ["nd:ndZ", "dz:100", "dz:100"]


async def test_liked_endpoint_and_starred_track_uids(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Track

    c = env["client"]
    async with session_scope() as s:
        uid = (await s.execute(select(Track.uid).where(Track.nd_song_id == "ndK"))).scalar_one()
    r = await c.post("/api/star", json={"uids": [uid]})
    assert r.status_code == 200, r.text
    starred = (await c.get("/api/starred")).json()
    assert starred["track_uids"] == [uid]
    liked = (await c.get("/api/library/liked")).json()
    assert [t["uid"] for t in liked["tracks"]] == [uid]
    assert liked["albums"] == [] and liked["artists"] == []


async def test_album_like_by_hub_uid_reaches_the_library_liked(env):
    """The library pages ♥ an album by its Hub `uid` (a bare `ref`): `/api/library/liked` finds it, and nothing leaks
    into the deprecated ND fields (the PWA of stage 6 has no Navidrome album id on that branch)."""
    c = env["client"]
    album = (await c.post("/api/library/albums", json={"title": "Группа крови", "album_artist": "Кино"})).json()
    r = await c.post("/api/star", json={"targets": [{"target_type": "album", "ref": album["uid"]}]})
    assert r.status_code == 200, r.text
    liked = (await c.get("/api/library/liked")).json()
    assert [a["uid"] for a in liked["albums"]] == [album["uid"]]
    starred = (await c.get("/api/starred")).json()
    assert starred["album_ids"] == []  # no UUID in the deprecated Navidrome ids
    assert any(s["target_type"] == "album" and s["liked"] for s in starred["subjects"])


async def test_artist_like_by_hub_uid_reaches_the_library_liked(env):
    """The library pages ♥ an artist by its Hub `uid` (bare ref): `/api/library/liked` finds it, and no UUID leaks into
    the deprecated `artist_ids`."""
    c = env["client"]
    album = (await c.post("/api/library/albums", json={"title": "Группа крови", "album_artist": "Кино"})).json()
    artist_uid = album["artist_uid"]
    assert artist_uid
    r = await c.post("/api/star", json={"targets": [{"target_type": "artist", "ref": artist_uid}]})
    assert r.status_code == 200, r.text
    liked = (await c.get("/api/library/liked")).json()
    assert artist_uid in [a["uid"] for a in liked["artists"]]
    starred = (await c.get("/api/starred")).json()
    assert starred["artist_ids"] == []  # no UUID in the deprecated Navidrome ids
    assert any(x["target_type"] == "artist" and x["liked"] for x in starred["subjects"])


async def test_trashed_track_is_not_an_index_in_a_playlist(env):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Track

    c = env["client"]
    await add_track("Кино", "Звезда", "ndZ")
    uid = (await c.post("/api/playlists", json={"name": "Дорога"})).json()["id"]
    await c.post(f"/api/playlists/{uid}/add", json={"song_ids": ["ndK", "ndZ"]})
    # the first track goes to the trash: the page drops it and `/remove` numbers only what the page shows
    async with session_scope() as s:
        track = (await s.execute(select(Track).where(Track.nd_song_id == "ndK"))).scalar_one()
        track.deleted_at = datetime.now(UTC)
    page = (await c.get(f"/api/playlists/{uid}")).json()
    assert page["song_count"] == len(page["tracks"]) == 1
    listed = next(p for p in (await c.get("/api/playlists")).json() if p["id"] == uid)
    assert listed["song_count"] == 1  # the list agrees with the page: the trashed track is not counted
    assert [t["ref"] for t in page["tracks"]] == ["nd:ndZ"]
    assert (await c.post(f"/api/playlists/{uid}/remove", json={"indexes": [0]})).json()["removed"] == 1
    assert (await c.get(f"/api/playlists/{uid}")).json()["tracks"] == []


async def test_parallel_playlist_inserts_keep_every_item(env):
    import asyncio

    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Track
    from app.playlists import add_tracks, get_by_uid
    from app.playlists import items as playlist_items

    c = env["client"]
    await add_track("Кино", "Звезда", "ndZ")
    uid = (await c.post("/api/playlists", json={"name": "Параллельный"})).json()["id"]
    async with session_scope() as s:
        pl = await get_by_uid(s, uid)
        assert pl is not None
        track = (await s.execute(select(Track).where(Track.nd_song_id == "ndK"))).scalar_one()
        playlist_id, track_uid = pl.id, track.uid

    async def one() -> None:
        async with session_scope() as s:
            pl = await get_by_uid(s, uid)
            assert pl is not None
            await add_tracks(s, pl, [track_uid])

    await asyncio.gather(*(one() for _ in range(6)))
    async with session_scope() as s:
        rows = await playlist_items(s, playlist_id)
    assert len(rows) == 6 and len({i.uid for i in rows}) == 6


async def test_playlist_rebalance_keeps_the_order(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Track
    from app.playlists import add_tracks, get_by_uid
    from app.playlists import items as playlist_items

    c = env["client"]
    await add_track("Кино", "Звезда", "ndZ")
    uid = (await c.post("/api/playlists", json={"name": "Длинный"})).json()["id"]
    await c.post(f"/api/playlists/{uid}/add", json={"song_ids": ["ndK", "ndZ"]})
    async with session_scope() as s:
        pl = await get_by_uid(s, uid)
        assert pl is not None
        rows = await playlist_items(s, pl.id)
        anchor, playlist_id = rows[0].uid, pl.id
        track = (await s.execute(select(Track).where(Track.nd_song_id == "ndK"))).scalar_one()
        track_uid = track.uid

    # halving the gap towards the next item exhausts it and forces a rebalance
    async with session_scope() as s:
        pl = await get_by_uid(s, uid)
        assert pl is not None
        for _ in range(60):
            created = await add_tracks(s, pl, [track_uid], anchor_uid=anchor)
            anchor = created[0].uid
    async with session_scope() as s:
        rows = await playlist_items(s, playlist_id)
    positions = [i.position for i in rows]
    assert positions == sorted(positions) and len(set(positions)) == len(positions)


async def test_playlist_unknown_is_404(env):
    c = env["client"]
    assert (await c.get("/api/playlists/nope")).status_code == 404
    assert (await c.post("/api/playlists/nope/add", json={"song_ids": ["ndK"]})).status_code == 404
    assert (await c.delete("/api/playlists/nope")).status_code == 404


async def test_navidrome_unreachable_is_502(env):
    env["state"]["down"] = True
    c = env["client"]
    assert (await c.get("/api/nd/home")).status_code == 502
    assert (await c.get("/api/nd/cover/al-alK")).status_code == 502


async def test_cover_is_proxied_as_the_user(env):
    r = await env["client"].get("/api/nd/cover/al-alK", params={"size": 300})
    assert r.status_code == 200 and r.content == b"\xff\xd8jpeg" and r.headers["content-type"] == "image/jpeg"
    p = [p for n, p in env["state"]["calls"] if n == "getCoverArt"][-1]
    assert (p["id"], p["size"], p["u"]) == ("al-alK", "300", "admin")


async def test_library_needs_a_session(hub_env):
    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        async with app_client(app, login=None) as client:
            assert (await client.get("/api/nd/home")).status_code == 401
