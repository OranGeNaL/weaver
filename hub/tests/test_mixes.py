"""«Микс дня» in the Hub (app/mixes/core.py, taste.py, api/mixes.py): the taste profile, the build with its quiet
downloads and the Hub playlist, the reserve, the replacement with its deletion rule, the API, the schedule.

The generators of the build are fakes (`fake_generators`): the songs the library has and new catalog tracks dz:5000… .
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select

from app.catalog.models import CatalogTrack
from tests.support import FakeDeezer, app_client

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
async def env(hub_env, tmp_path: Path):
    from app.main import create_app
    from app.services import svc

    (tmp_path / "none.yaml").write_text("mixes:\n  size: 6\n  reserve: 2\n", encoding="utf-8")
    from app.config import get_settings

    get_settings.cache_clear()  # importing app.main creates the module's app and may cache defaults
    app = create_app()
    async with app.router.lifespan_context(app):
        svc.deezer = cast(Any, FakeDeezer())
        async with app_client(app) as client:
            yield {"app": app, "client": client, "music": hub_env, "admin": await user_id("admin")}


async def user_id(name: str) -> int:
    from app.db import session_scope
    from app.models import User

    async with session_scope() as s:
        return (await s.execute(select(User.id).where(User.username == name))).scalar_one()


async def add_user(name: str) -> int:
    from app.db import session_scope
    from app.models import User
    from app.passwords import hash_password

    async with session_scope() as s:
        u = User(username=name, password_hash=hash_password("pw"))
        s.add(u)
        await s.flush()
        return u.id


async def add_track(n: int, *, music: Path | None = None, deezer: bool = True) -> int:
    """A library song «Свой n — Песня n» (Hub uid u<n>, Deezer dz:<n> unless `deezer` is false)."""
    from app.db import session_scope
    from app.matching.normalize import fingerprint
    from app.models import Track

    path = f"Свой {n}/Песня {n}.mp3"
    if music is not None:
        f = music / "library" / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"mp3")
    async with session_scope() as s:
        t = Track(
            uid=f"u{n}",
            path=path,
            artist=f"Свой {n}",
            title=f"Песня {n}",
            duration_s=200,
            deezer_track_id=str(n) if deezer else None,
            fingerprint=fingerprint(f"Свой {n}", f"Песня {n}"),
        )
        s.add(t)
        await s.flush()
        return t.id


async def add_event(
    user: int,
    kind: str,
    *,
    uid: str | None = None,
    ref: str | None = None,
    at: datetime | None = None,
    position: float | None = None,
) -> None:
    from app.db import session_scope
    from app.models import PlayEvent

    async with session_scope() as s:
        s.add(
            PlayEvent(
                user_id=user,
                event_id=uuid.uuid4().hex,
                ts=at or NOW,
                kind=kind,
                ref=ref,
                track_uid=uid,
                position_s=position,
                client="app/1.5.0",
            )
        )


def new_track(n: int) -> CatalogTrack:
    return CatalogTrack(
        ref=f"dz:{n}",
        source="dz",
        id=str(n),
        title=f"Новая {n}",
        artist=f"Новый {n}",
        artists=[f"Новый {n}"],
        duration_s=200,
    )


def fake_generators(monkeypatch: pytest.MonkeyPatch, new: list[CatalogTrack]) -> None:
    from app.mixes import build

    def gens(p, seeds):
        async def fav():
            return build.favorites(p)

        async def radio():
            return new

        return {"favorites": fav, "ytm_radio": radio}

    monkeypatch.setattr(build, "generators", gens)


async def history(user: int, n: int = 4) -> None:
    """The user listened to library songs 1..n ten days ago."""
    for i in range(1, n + 1):
        await add_track(i)
        await add_event(user, "finish", uid=f"u{i}", at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10))


# --- the profile ---------------------------------------------------------------------------------------------------


async def test_profile_weights_and_decay(env) -> None:
    """M4 (2026-10-23): ♥ +1, finish +2, skip before 30 s −1, a later skip up to +2 by how much was heard,
    «Не нравится» −5, an own download 0; half the weight in 30 days; a ♥ that an import set is not the user's."""
    from app.db import session_scope
    from app.mixes.taste import build_profile
    from app.models import Dislike, DownloadBatch, DownloadItem, Import, User

    me = env["admin"]
    for n in range(1, 10):
        await add_track(n)
    await add_event(me, "finish", uid="u1", at=NOW)
    await add_event(me, "finish", uid="u2", at=NOW - timedelta(days=30))
    await add_event(me, "skip", uid="u3", at=NOW, position=10)
    await add_event(me, "skip", uid="u3", at=NOW, position=100)  # after 30 s: 70 of the 170 s past them (file: 200 s)
    await add_event(me, "like", uid="u7", at=NOW - timedelta(days=1))
    await add_event(me, "like", uid="u4", at=NOW)  # ♥ without a playback event; its own download adds nothing
    await add_event(me, "skip", uid="u8", at=NOW, position=199)  # a second before the end
    async with session_scope() as s:
        batch = DownloadBatch(user_id=me, origin="catalog")
        s.add(batch)
        await s.flush()
        s.add(
            DownloadItem(
                batch_id=batch.id,
                user_id=me,
                status="done",
                track_id=4,
                created_at=NOW,
                query_json={"query": {}, "meta": {"track_ref": "dz:4"}},
            )
        )
        s.add(
            DownloadItem(
                batch_id=batch.id,
                user_id=me,
                status="done",
                track_id=6,
                created_at=NOW,
                query_json={"query": {}, "meta": {"track_ref": "dz:6"}, "mix": {"id": 1}},
            )
        )  # not own
        s.add(
            DownloadItem(
                batch_id=batch.id,
                user_id=me,
                status="done",
                track_id=9,
                created_at=NOW,
                query_json={"query": {}, "meta": {"track_ref": "dz:9"}},
            )
        )
        s.add(Dislike(user_id=me, ref="lib:u5", source="app", created_at=NOW))
        s.add(Import(user_id=me, source="yandex", method="token", options_json={"liked_uids": ["u6"]}))
        user = await s.get(User, me)
        assert user is not None
        assert user is not None
        p = await build_profile(
            s,
            user,
            now=NOW,
        )

    score = {t.uid: round(t.score, 3) for t in p.tracks.values()}
    assert score == {
        "u1": 2.0,
        "u2": 1.0,
        "u3": -0.702,  # −1 + 2 · (70/170) / (1 + 3 · 100/170)
        "u4": 1.0,  # ♥ without an event; its own download adds nothing
        "u5": -5.0,
        "u7": 0.977,  # the ♥ event a day ago
        "u8": 1.954,  # a skip at the very end counts almost as a finish
        "u9": 0.0,  # only downloaded
    }  # u6: the import's ♥, a mix's download
    assert p.tracks["lib:u4"].liked and p.tracks["lib:u4"].downloaded and p.tracks["lib:u7"].liked
    assert p.find(ref="dz:1") is p.tracks["lib:u1"]  # known by its catalog id too
    # u2 and u4 tie at 1.0: the profile's order (events first)
    assert [t.uid for t in p.seeds(8)] == ["u1", "u8", "u2", "u4", "u7"]
    assert p.disliked(ref=None, uid="u5", artist="x", artist_ref=None)


async def test_v1_profile_does_not_count_an_imported_like(env) -> None:
    """An import's ♥ is provenance: v1 must neither score it nor mark the track liked (the v2 path already does)."""
    from app.api.schemas import FavoriteTarget
    from app.db import session_scope
    from app.favorites import record
    from app.mixes.taste import build_profile
    from app.models import Import, ImportCollection, ImportItem, User

    me = env["admin"]
    tid = await add_track(10)
    async with session_scope() as s:
        user = await s.get(User, me)
        assert user is not None
        assert user is not None
        imp = Import(user_id=me, source="yandex", method="token", status="done")
        s.add(imp)
        await s.flush()
        coll = ImportCollection(import_id=imp.id, kind="likes", external_id="likes", title="Likes")
        s.add(coll)
        await s.flush()
        s.add(
            ImportItem(
                import_id=imp.id,
                collection_id=coll.id,
                position=0,
                external_id="1",
                ref="dz:10",
                status="done",
                track_id=tid,
            )
        )
        await record(s, me, FavoriteTarget(ref="dz:10"), True, operation="import-like", client="import", ts=NOW)
        profile = await build_profile(s, user, now=NOW, model="v1")

    track = profile.find(ref="dz:10")
    assert track is not None
    assert not track.liked and track.score == 0

    # a later own like of the same track overrides the imported provenance, as in v2
    async with session_scope() as s:
        await record(s, me, FavoriteTarget(ref="dz:10"), True, operation="own-relike", client="pwa", ts=NOW)
        profile = await build_profile(s, user, now=NOW, model="v1")
    track = profile.find(ref="dz:10")
    assert track is not None and track.liked and track.score > 0


async def test_own_download_alone_weighs_nothing_but_stays_in_the_profile(env) -> None:
    """A download without other events: score 0 — not a favorite and not in the sound profile, but not a negative
    either: the stash and the fallback seeds take it, and the profile is not empty."""
    from app.db import session_scope
    from app.mixes.build import favorites, stash
    from app.mixes.taste import build_profile
    from app.models import DownloadBatch, DownloadItem, User

    me = env["admin"]
    await add_track(1)
    async with session_scope() as s:
        batch = DownloadBatch(user_id=me, origin="catalog")
        s.add(batch)
        await s.flush()
        s.add(
            DownloadItem(
                batch_id=batch.id,
                user_id=me,
                status="done",
                track_id=1,
                created_at=NOW,
                query_json={"query": {}, "meta": {"track_ref": "dz:1"}},
            )
        )
        await s.flush()
        user = await s.get(User, me)
        assert user is not None
        assert user is not None
        p = await build_profile(s, user, now=NOW)

    t = p.tracks["lib:u1"]
    assert t.downloaded and t.score == 0.0
    assert favorites(p) == [] and p.vector_profile() == {}
    assert [x.ref for x in stash(p)] == ["dz:1"]
    assert [x.uid for x in p.seeds(8)] == ["u1"] and not p.empty


def test_skip_weight_rises_slowly_then_steeply() -> None:
    """A skip after 30 s: 0 right after them, +2 at the very end, a hyperbola between (slow first, steep last)."""
    from app.mixes.taste import skip_weight

    w = [round(skip_weight(30 + 170 * x, 200), 3) for x in (0, 0.25, 0.5, 0.75, 0.9, 1)]
    assert w == [0.0, 0.154, 0.4, 0.857, 1.385, 2.0]
    assert skip_weight(250, 200) == 2.0  # a position past the length (rounding) is the end
    assert skip_weight(29, 200) == 0.0 and skip_weight(40, None) == 0.0 and skip_weight(25, 30) == 0.0


async def test_skip_after_30_s_takes_the_length_of_the_event(env) -> None:
    from app.db import session_scope
    from app.mixes.taste import build_profile
    from app.models import PlayEvent, User

    me = env["admin"]
    await add_track(1)  # the file says 200 s, the player said 130 s: the event wins
    async with session_scope() as s:
        s.add(
            PlayEvent(
                user_id=me,
                event_id="e1",
                ts=NOW,
                kind="skip",
                track_uid="u1",
                position_s=80,
                duration_s=130,
                client="app/1.6.0",
            )
        )
        await s.flush()
        user = await s.get(User, me)
        assert user is not None
        assert user is not None
        p = await build_profile(s, user, now=NOW)

    assert round(p.tracks["lib:u1"].score, 3) == 0.4  # x = 50 / 100: 2 · 0.5 / (1 + 3 · 0.5)


async def test_cold_start_seeds_from_picked_artists(env) -> None:
    from app.db import session_scope
    from app.mixes.build import cold_seeds
    from app.mixes.taste import build_profile
    from app.models import MixSettings, User

    async with session_scope() as s:
        s.add(
            MixSettings(user_id=env["admin"], mode="normal", mix_hour=5, artists_json=[{"ref": "dz:1", "name": "Кино"}])
        )
        await s.flush()
        user = await s.get(User, env["admin"])
        assert user is not None
        assert user is not None
        p = await build_profile(s, user)

    assert not p.empty and not p.seeds(8)
    assert [(t.artist, t.title) for t in await cold_seeds(p, 8)] == [("Кино", "Группа крови")]


# --- the build, downloads, playlist -------------------------------------------------------------------------------


async def mix_rows(mix_id: int) -> list:
    from app.db import session_scope
    from app.models import MixItem

    async with session_scope() as s:
        return list(
            (await s.execute(select(MixItem).where(MixItem.mix_id == mix_id).order_by(MixItem.position))).scalars()
        )


@pytest.mark.parametrize("shadow", ["off", "on", "error", "snapshot_error"])
async def test_build_queues_quiet_downloads_and_writes_the_playlist(env, monkeypatch, shadow) -> None:
    from app import listen
    from app.config import get_settings
    from app.db import session_scope
    from app.mixes import core, taste_snapshot
    from app.mixes import shadow as shadow_module
    from app.models import DownloadItem, Job, MixSettings

    monkeypatch.setattr(get_settings().cfg.mixes, "taste_shadow", shadow != "off")
    if shadow == "error":

        def fail(*args, **kwargs):
            raise RuntimeError("diagnostic failure")

        monkeypatch.setattr(shadow_module, "compare", fail)

    if shadow == "snapshot_error":

        async def fail_snapshot(*args, **kwargs):
            raise RuntimeError("snapshot failure")

        monkeypatch.setattr(taste_snapshot, "load_snapshot", fail_snapshot)

    warmed: list[list[str]] = []
    monkeypatch.setattr(listen, "warm_light", lambda refs: warmed.append(list(refs)))
    me = env["admin"]
    await history(me)
    fake_generators(monkeypatch, [new_track(5000 + i) for i in range(10)])

    mix = await core.build_for(me)

    assert mix is not None

    if shadow == "off":
        assert "taste_shadow" not in mix.stats_json
    elif shadow in ("error", "snapshot_error"):
        assert mix.stats_json["taste_shadow"]["error"] == (
            "comparison_failed" if shadow == "error" else "snapshot_failed"
        )
    else:
        assert mix.stats_json["taste_shadow"]["schema"] == 1
        assert mix.stats_json["taste_shadow"]["sound"] is None
    rows = await mix_rows(mix.id)
    active = [r for r in rows if r.state == "active"]
    from app.models import Track

    async with session_scope() as s:
        library_uids = {
            t.id: t.uid
            for t in (
                await s.execute(select(Track).where(Track.id.in_([r.track_id for r in active if r.track_id])))
            ).scalars()
        }
    # the light copies for a metered network are made ahead: the songs of the mix the library has
    assert warmed and warmed[-1] == [f"lib:{library_uids[r.track_id]}" for r in active if r.track_id]
    assert len(active) == 6 and len([r for r in rows if r.state == "reserve"]) == 2
    assert sum(r.familiar for r in active) == 2  # «Обычный»: 40 % of 6
    assert active[0].familiar and active[0].track_id in library_uids and active[0].generator == "favorites"
    async with session_scope() as s:
        dls = list((await s.execute(select(DownloadItem))).scalars())
        jobs = list((await s.execute(select(Job).where(Job.kind == "download_item"))).scalars())
        settings = await s.get(MixSettings, me)
        assert settings is not None
    # the new songs are downloaded quietly, in the background; the library's are not
    assert {d.query_json["meta"]["track_ref"] for d in dls} == {r.ref for r in active if not r.familiar}
    assert all(d.query_json["mix"]["id"] == mix.id for d in dls) and {j.priority for j in jobs} == {-10}
    assert all(r.downloaded_for_mix for r in active if not r.familiar)
    r = await env["client"].get("/api/downloads")
    assert r.status_code == 200 and all(x["star"] for x in r.json())  # clients hide them
    # the playlist «Микс дня» is a local system playlist with the songs the library has, in the mix's order
    from app.playlists import get_owned
    from app.playlists import items as playlist_items

    async with session_scope() as s:
        settings = await s.get(MixSettings, me)
        assert settings is not None and settings.playlist_uid is not None
        pl = await get_owned(s, me, settings.playlist_uid)
        rows_pl = await playlist_items(s, pl.id) if pl else []
    assert pl is not None and pl.name == "Микс дня" and pl.system
    assert len(rows_pl) == len([r for r in active if r.track_id])
    assert mix.playlist_uid == settings.playlist_uid


async def test_a_song_that_did_not_download_gives_its_place_to_the_reserve(env, monkeypatch) -> None:
    from app.db import session_scope
    from app.jobs.downloads import update_item
    from app.mixes import core
    from app.models import DownloadItem, Job, MixSettings

    me = env["admin"]
    await history(me)
    fake_generators(monkeypatch, [new_track(5000 + i) for i in range(10)])
    mix = await core.build_for(me)
    assert mix is not None
    rows = await mix_rows(mix.id)
    failed, done = [r for r in rows if r.state == "active" and r.download_item_id][:2]

    await update_item(failed.download_item_id, status="needs_review", error="Не найдено ни в одном источнике")
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Job).where(Job.kind == "mix_sync"))).scalar() == 1
    tid = await add_track(5999, music=None)
    async with session_scope() as s:
        dl = await s.get(DownloadItem, done.download_item_id)
        assert dl is not None
        dl.status, dl.track_id = "done", tid
    await core.sync(mix.id)

    rows = {r.id: r for r in await mix_rows(mix.id)}
    sub = next(r for r in rows.values() if r.position == failed.position and r.id != failed.id)
    assert rows[failed.id].state == "failed" and sub.state == "active" and not sub.familiar
    assert sub.download_item_id  # the substitute is downloaded now
    from app.models import Track
    from app.playlists import get_owned
    from app.playlists import track_uids as playlist_uids

    async with session_scope() as s:
        mix_settings = await s.get(MixSettings, me)
        assert mix_settings is not None and mix_settings.playlist_uid is not None
        pl = await get_owned(s, me, mix_settings.playlist_uid)
        assert pl is not None
        want_row = await s.get(Track, tid)
        assert want_row is not None
        assert want_row.uid in await playlist_uids(s, pl.id)


async def test_next_mix_keeps_the_playlist_and_closes_the_old_one(env, monkeypatch) -> None:
    from app.db import session_scope
    from app.mixes import core
    from app.models import Mix

    me = env["admin"]
    await history(me)
    fake_generators(monkeypatch, [new_track(5000 + i) for i in range(10)])
    first = await core.build_for(me)
    assert first is not None
    second = await core.build_for(me)
    assert second is not None

    async with session_scope() as s:
        old = await s.get(Mix, first.id)
        assert old is not None
    from app.models import MixSettings, Playlist

    assert old.status == "closed" and second.status == "active"
    assert second.playlist_uid == first.playlist_uid
    async with session_scope() as s:
        st = await s.get(MixSettings, me)
        assert st is not None
        assert st.playlist_uid == first.playlist_uid
        assert (
            await s.execute(select(func.count()).select_from(Playlist).where(Playlist.owner_id == me))
        ).scalar() == 1


async def test_no_history_no_mix(env) -> None:
    from app.mixes import core

    assert await core.build_for(env["admin"]) is None


# --- the replacement: what may be deleted (6.3) --------------------------------------------------------------------


async def old_mix_with(user: int, track_id: int, *, for_mix: bool = True, ref: str = "dz:7") -> int:
    from app.db import session_scope
    from app.models import Mix, MixItem

    async with session_scope() as s:
        m = Mix(user_id=user, title="Микс", description="", mode="normal", status="closed")
        s.add(m)
        await s.flush()
        s.add(
            MixItem(
                mix_id=m.id,
                position=0,
                state="active",
                ref=ref,
                track_id=track_id,
                generator="ytm_radio",
                downloaded_for_mix=for_mix,
            )
        )
        return m.id


async def keep_by(case: str, env, me: int, other: int, track_id: int) -> None:
    from app.db import session_scope
    from app.models import DownloadBatch, DownloadItem, Import, ImportCollection, ImportItem, Mix, MixItem

    if case == "listened":
        await add_event(other, "finish", uid="u7")
    elif case == "listened 30 s":
        await add_event(other, "skip", ref="dz:7", position=31)
    elif case == "starred":
        from app.api.schemas import FavoriteTarget
        from app.favorites import record
        from app.models import Track

        async with session_scope() as s:
            t = await s.get(Track, track_id)
            assert t is not None
            await record(s, other, FavoriteTarget(uid=t.uid, ref="dz:7"), True, client="pwa")
    elif case == "in a playlist":
        from app.models import Playlist, PlaylistItem, Track

        async with session_scope() as s:
            t = await s.get(Track, track_id)
            assert t is not None
            pl = Playlist(uid=f"pl-{track_id}", owner_id=other, name="Моё")
            s.add(pl)
            await s.flush()
            s.add(PlaylistItem(uid=f"pli-{track_id}", playlist_id=pl.id, track_uid=t.uid, position=1024))
    elif case == "asked to download":
        async with session_scope() as s:
            b = DownloadBatch(user_id=other, origin="catalog")
            s.add(b)
            await s.flush()
            s.add(
                DownloadItem(
                    batch_id=b.id,
                    user_id=other,
                    status="done",
                    track_id=track_id,
                    query_json={"query": {}, "meta": {"track_ref": "dz:7"}},
                )
            )
    elif case == "another mix":
        async with session_scope() as s:
            m = Mix(user_id=other, title="Микс", description="", mode="normal", status="active")
            s.add(m)
            await s.flush()
            s.add(MixItem(mix_id=m.id, position=3, state="active", ref="dz:7", generator="favorites"))
    elif case == "an import":
        async with session_scope() as s:
            imp = Import(user_id=other, source="yandex", method="token")
            s.add(imp)
            await s.flush()
            c = ImportCollection(import_id=imp.id, kind="likes", external_id="3", title="Мне нравится")
            s.add(c)
            await s.flush()
            s.add(ImportItem(import_id=imp.id, collection_id=c.id, external_id="y7", ref="dz:7", status="waiting"))


@pytest.mark.parametrize(
    "case",
    [
        "listened",
        "listened 30 s",
        "starred",
        "in a playlist",
        "asked to download",
        "another mix",
        "an import",
    ],
)
async def test_a_song_of_the_old_mix_stays(env, case: str) -> None:
    from app.db import session_scope
    from app.mixes import core
    from app.models import Track

    me, other = env["admin"], await add_user("other")
    tid = await add_track(7, music=env["music"])
    mix_id = await old_mix_with(me, tid)
    await keep_by(case, env, me, other, tid)

    assert await core.remove_unheard([mix_id]) == 0
    async with session_scope() as s:
        assert await s.get(Track, tid) is not None
    assert (env["music"] / "library" / "Свой 7" / "Песня 7.mp3").is_file()


async def test_a_song_another_mix_holds_goes_when_that_mix_closes(env) -> None:
    from app.db import session_scope
    from app.mixes import core
    from app.models import Mix

    me, other = env["admin"], await add_user("other")
    tid = await add_track(7, music=env["music"])
    mix_id = await old_mix_with(me, tid)
    await keep_by("another mix", env, me, other, tid)
    assert await core.remove_unheard([mix_id]) == 0
    async with session_scope() as s:
        for m in (await s.execute(select(Mix).where(Mix.user_id == other))).scalars():
            m.status = "closed"

    assert await core.pending_cleanup(me) == [mix_id]
    assert await core.remove_unheard([mix_id]) == 1
    assert await core.pending_cleanup(me) == []


async def test_closing_cancels_queued_downloads_and_waits_for_running_ones(env, monkeypatch) -> None:
    from app.db import session_scope
    from app.mixes import core
    from app.models import DownloadItem

    me = env["admin"]
    await history(me)
    fake_generators(monkeypatch, [new_track(5000 + i) for i in range(10)])
    first = await core.build_for(me)
    assert first is not None
    running, queued = [r for r in await mix_rows(first.id) if r.download_item_id][:2]
    async with session_scope() as s:
        running_row = await s.get(DownloadItem, running.download_item_id)
        assert running_row is not None
        running_row.status = "downloading"
    fake_generators(monkeypatch, [new_track(6000 + i) for i in range(10)])  # other new songs today

    await core.build_for(me)

    async with session_scope() as s:
        queued_row = await s.get(DownloadItem, queued.download_item_id)
        assert queued_row is not None
        assert queued_row.status == "cancelled"
        dl = await s.get(DownloadItem, running.download_item_id)
        assert dl is not None
        assert dl.status == "downloading"
        dl.status, dl.track_id = "done", await add_track(5998)  # it finished after the mix closed
    assert await core.pending_cleanup(me) == [first.id]
    assert await core.remove_unheard([first.id]) == 1  # decided at the next closing
    assert await core.pending_cleanup(me) == []


async def test_a_song_that_was_in_the_library_before_stays(env) -> None:
    from app.mixes import core

    tid = await add_track(7, music=env["music"])
    mix_id = await old_mix_with(env["admin"], tid, for_mix=False)

    assert await core.remove_unheard([mix_id]) == 0


async def test_an_unheard_song_goes_to_the_trash(env) -> None:
    from app.db import session_scope
    from app.mixes import core
    from app.models import Track

    me, other = env["admin"], await add_user("other")
    tid = await add_track(7, music=env["music"])
    mix_id = await old_mix_with(me, tid)
    await add_event(other, "skip", uid="u7", position=12)  # skipped early: not listened
    await add_event(me, "start", uid="u7")

    assert await core.remove_unheard([mix_id]) == 1
    async with session_scope() as s:
        trashed = await s.get(Track, tid)
        assert trashed is not None
        # trash marks the row; the blob is untouched until `trash_days` elapse and GC frees it
        assert trashed is not None and trashed.deleted_at is not None


# --- the API ---------------------------------------------------------------------------------------------------------


async def test_today_settings_and_rebuild(env, monkeypatch) -> None:
    from app.mixes import core

    client = env["client"]
    r = await client.get("/api/mixes/today")
    assert r.status_code == 200, r.text
    assert r.json()["mix"] is None and r.json()["onboarding"] is True
    assert r.json()["settings"]["mode"] == "normal" and r.json()["settings"]["mix_hour"] == 5

    await history(env["admin"])
    fake_generators(monkeypatch, [new_track(5000 + i) for i in range(10)])
    mix = await core.build_for(env["admin"])
    assert mix is not None
    r = await client.get("/api/mixes/today")
    body = r.json()["mix"]
    assert body["id"] == mix.id and body["total"] == 6 and body["mode_title"] == "Обычный"
    first = body["tracks"][0]
    assert first["owned"] and first["uid"] and first["generator_title"] == "Любимое" and first["familiar"]
    assert {t["generator"] for t in body["tracks"]} == {"favorites", "ytm_radio"}
    assert body["ready"] == sum(1 for t in body["tracks"] if t["uid"])
    assert (await client.get(f"/api/mixes/{mix.id}")).json()["tracks"] == body["tracks"]

    r = await client.put("/api/mixes/settings", json={"mode": "discover", "mix_hour": 7})
    assert r.status_code == 200 and (r.json()["mode"], r.json()["mix_hour"]) == ("discover", 7)
    assert (await client.put("/api/mixes/settings", json={"mode": "loud"})).status_code == 422
    assert (await client.put("/api/mixes/settings", json={"mix_hour": 24})).status_code == 422

    r = await client.post("/api/mixes/rebuild", json={})
    assert r.status_code == 200 and r.json() == {"queued": True}
    assert (await client.post("/api/mixes/rebuild", json={})).json() == {"queued": False}  # one at a time
    assert (await client.get("/api/mixes/today")).json()["building"] is True


async def test_rebuild_is_for_the_admin_and_mixes_are_private(env, monkeypatch) -> None:
    from app.mixes import core

    other = await add_user("other")
    await history(other)
    fake_generators(monkeypatch, [new_track(5000 + i) for i in range(10)])
    mix = await core.build_for(other)
    assert mix is not None

    async with app_client(env["app"], login=("other", "pw")) as c:
        assert (await c.post("/api/mixes/rebuild", json={})).status_code == 403
        assert (await c.get(f"/api/mixes/{mix.id}")).status_code == 200
    r = await env["client"].post("/api/mixes/rebuild", json={"user_id": other})  # the admin may
    assert r.json() == {"queued": True}


async def test_onboarding_picks_artists_and_starts_a_build(env) -> None:
    from app.db import session_scope
    from app.models import Job

    client = env["client"]
    r = await client.get("/api/taste/onboarding")
    assert r.status_code == 200, r.text
    assert r.json()["needed"] is True and [a["name"] for a in r.json()["suggestions"]] == ["Земфира", "Кино"]

    r = await client.post("/api/taste/artists", json={"artist_refs": ["dz:1"]})
    assert r.status_code == 200, r.text
    assert r.json()["artists"] == [{"ref": "dz:1", "name": "Кино"}]
    assert (await client.post("/api/taste/artists", json={"artist_refs": ["dz:999"]})).status_code == 404
    assert (await client.post("/api/taste/artists", json={"artist_refs": ["hm:1"]})).status_code == 400
    async with session_scope() as s:
        jobs = list((await s.execute(select(Job).where(Job.kind == "mix_build"))).scalars())
    assert [(j.payload_json, j.priority) for j in jobs] == [({"user_id": env["admin"]}, 0)]
    r = await client.get("/api/taste/onboarding")
    assert r.json()["needed"] is False and r.json()["selected"] == [{"ref": "dz:1", "name": "Кино"}]


# --- the schedule ------------------------------------------------------------------------------------------------------


async def test_schedule_builds_once_a_day_after_the_hour(env) -> None:
    from app.mixes import core

    me = env["admin"]
    quiet = await add_user("quiet")  # no history, no picked artists: no mix
    await history(me, 1)
    before = datetime(2026, 10, 23, 1, 30, tzinfo=UTC)  # 04:30 in Moscow
    after = datetime(2026, 10, 23, 2, 30, tzinfo=UTC)  # 05:30

    assert await core.due_users(before) == []
    assert await core.due_users(after) == [me]
    assert await core.due_users(after + timedelta(hours=1)) == []  # once a day
    assert await core.due_users(after + timedelta(days=1)) == []  # still queued: one build at a time
    assert quiet not in await core.due_users(after + timedelta(days=2))


@pytest.mark.parametrize("shadow", [False, True])
async def test_v2_shadow_does_not_repeat_build_services(env, monkeypatch, shadow):
    from app.config import get_settings
    from app.db import session_scope
    from app.mixes import build, core
    from app.models import DownloadItem, Job

    cfg = get_settings().cfg.mixes
    monkeypatch.setattr(cfg, "taste_model", "v2")
    monkeypatch.setattr(cfg, "taste_shadow", shadow)
    await history(env["admin"])
    fake_generators(monkeypatch, [new_track(5000 + i) for i in range(10)])
    calls = {name: 0 for name in ("run_generators", "sound", "name_mix")}
    for name in calls:
        original = getattr(build, name)

        async def counted(*args, _name=name, _original=original, **kwargs):
            calls[_name] += 1
            return await _original(*args, **kwargs)

        monkeypatch.setattr(build, name, counted)
    mix = await core.build_for(env["admin"])
    assert mix is not None
    assert mix is not None and calls == {"run_generators": 1, "sound": 1, "name_mix": 1}
    if shadow:
        assert mix.stats_json["taste_shadow"]["active_seed_keys"] == mix.stats_json["seed_keys"]
    else:
        assert "taste_shadow" not in mix.stats_json
    rows = await mix_rows(mix.id)
    async with session_scope() as s:
        downloads = list((await s.execute(select(DownloadItem))).scalars())
        jobs = list((await s.execute(select(Job).where(Job.kind == "download_item"))).scalars())
    active_downloads = [row for row in rows if row.state == "active" and row.download_item_id]
    assert len(jobs) == len(downloads) == len(active_downloads)
