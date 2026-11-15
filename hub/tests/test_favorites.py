"""Permanent typed favorite state and reconciliation, with explicit clocks and no network."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.api.schemas import FavoriteTarget, PlayEventIn
from app.favorites import command, fold, record, snapshot
from app.models import FavoriteSubject, PlayEvent, User

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def event(eid, kind, at=NOW, *, received=None, typ="track", ref: str | None = "dz:1", nd=None, reverts=None, user=1):
    fields = {"track": "nd_song_id", "album": "nd_album_id", "artist": "nd_artist_id"}
    return PlayEvent(
        id=int(eid) if str(eid).isdigit() else 0,
        user_id=user,
        event_id=str(eid),
        kind=kind,
        target_type=typ,
        ref=ref,
        **{fields[typ]: nd},
        ts=at,
        received_at=received or at,
        client="app/1",
        reverts_event_id=reverts,
        mirror_pending=False,
    )


@pytest.mark.parametrize("typ", ["track", "album", "artist"])
def test_continuous_like_is_one_contribution_and_unlike_is_neutral(typ):
    rows = [event(1, "like", NOW - timedelta(days=900), typ=typ), event(2, "like", NOW - timedelta(days=1), typ=typ)]
    st = next(iter(fold(rows).values()))
    assert st.liked and st.since == rows[0].ts and st.event.event_id == "1"
    rows.append(event(3, "unlike", typ=typ))
    st = next(iter(fold(rows).values()))
    assert not st.liked and st.since is None
    rows.append(event(4, "like", NOW + timedelta(seconds=1), typ=typ))
    assert next(iter(fold(rows).values())).since == rows[-1].ts


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0)])
def test_compensation_arriving_before_decision_cancels_legacy_duplicate(order):
    rows = [
        event("promise", "like"),
        event("legacy", "like", NOW + timedelta(seconds=1)),
        event("rollback", "unlike", NOW + timedelta(seconds=2), reverts="promise"),
    ]
    st = next(iter(fold([rows[i] for i in order]).values()))
    assert not st.liked


def test_stale_rollback_cannot_undo_new_independent_like_or_other_target():
    rows = [
        event(1, "like"),
        event(2, "unlike", NOW + timedelta(seconds=1)),
        event(3, "like", NOW + timedelta(seconds=2)),
        event(4, "unlike", NOW + timedelta(seconds=3), reverts="1"),
        event(5, "unlike", NOW + timedelta(seconds=4), ref="dz:2", reverts="3"),
    ]
    states = fold(rows)
    assert states[("track", "ref:dz:1")].liked
    assert states[("track", "ref:dz:1")].since == rows[2].ts


def test_successful_retry_after_compensation_is_a_new_series():
    rows = [
        event(1, "like"),
        event(2, "like", NOW + timedelta(seconds=1)),
        event(3, "unlike", NOW + timedelta(seconds=2), reverts="1"),
        event(4, "like", NOW + timedelta(seconds=3)),
    ]
    state = next(iter(fold(rows).values()))
    assert state.liked and state.since == rows[-1].ts


def test_aliases_merge_replaced_songs_and_keep_types_separate():
    subject = FavoriteSubject(
        target_type="track",
        canonical_key="ref:dz:1",
        ref="dz:1",
        nd_id="new",
        aliases=["ref:dz:1", "nd:old", "nd:new"],
        data={},
    )
    states = fold(
        [
            event(1, "like", ref=None, nd="old"),
            event(2, "unlike", ref=None, nd="new"),
            event(3, "like", typ="album", ref="dz:1"),
        ],
        [subject],
    )
    assert len(states) == 2
    assert not states[("track", "nd:new")].liked
    assert states[("album", "ref:dz:1")].liked


def test_future_events_do_not_change_current_state():
    rows = [event(1, "like"), event(2, "unlike", NOW + timedelta(seconds=1))]
    assert next(iter(fold(rows, now=NOW).values())).liked


def test_event_intake_uses_three_calendar_years_including_leap_day():
    from app.plays import event_time

    now = datetime(2024, 2, 29, 12, tzinfo=UTC)
    assert event_time(datetime(2021, 2, 28, 12, tzinfo=UTC), now) == datetime(2021, 2, 28, 12, tzinfo=UTC)
    assert event_time(datetime(2021, 2, 28, 11, tzinfo=UTC), now) == now


@pytest.mark.parametrize(
    "payload",
    [
        {"target_type": "album", "nd_song_id": "s"},
        {"target_type": "artist", "nd_album_id": "a", "ref": "dz:1"},
        {"target_type": "album", "nd_album_id": "a", "kind": "start"},
        {"target_type": "album", "nd_album_id": "a", "kind": "dislike"},
        {"ref": "dz:1", "reverts_event_id": "self"},
        {"ref": "dz:1", "target_data": {"name": "x" * 8193}},
    ],
)
def test_invalid_typed_target_is_rejected(payload):
    with pytest.raises(ValueError):
        PlayEventIn.model_validate(dict(event_id="self", ts=NOW, kind="like", client="pwa") | payload)


@pytest.fixture
async def ledger(db_url: str):
    from app.db import dispose, init_engine, session_scope

    init_engine(db_url)
    async with session_scope() as s:
        user = User(username="qa", nd_username="qa", nd_password_enc="unused")
        other = User(username="other", nd_username="other", nd_password_enc="unused")
        s.add_all([user, other])
        await s.flush()
    yield user, other
    await dispose()


async def test_command_repeat_and_stale_request_do_not_overwrite_newer_decision(ledger):
    from app.db import session_scope

    user, _ = ledger
    async with session_scope() as s:
        nd = AsyncMock()
        target = FavoriteTarget(nd_song_id="s")
        await command(s, user, [target], liked=True, operation="one", ts=NOW, nd=nd)
        await command(s, user, [target], liked=False, operation="two", ts=NOW + timedelta(seconds=2), nd=nd)
        await command(s, user, [target], liked=True, operation="one", ts=NOW, nd=nd)
        await command(s, user, [target], liked=True, operation="late", ts=NOW - timedelta(seconds=2), nd=nd)
        assert not next(iter((await snapshot(s, user.id)).values())).liked


async def test_assistant_bulk_command_journals_every_type_locally(ledger):
    from app.db import session_scope

    user, _ = ledger
    async with session_scope() as s:
        await command(
            s,
            user,
            [FavoriteTarget(nd_song_id="s"), FavoriteTarget(target_type="album", nd_album_id="a")],
            liked=True,
            client="assistant",
            context="assistant:bulk",
        )
        rows = list((await s.execute(select(PlayEvent))).scalars())
        assert {r.target_type for r in rows} == {"track", "album"}
        assert all(r.client == "assistant" and r.context == "assistant:bulk" for r in rows)


async def test_v1_profile_counts_one_server_and_legacy_like_and_neutral_unlike(ledger):
    from app.db import session_scope
    from app.mixes.taste import build_profile
    from app.models import utcnow
    from app.plays import record_events

    user, _ = ledger
    async with session_scope() as s:
        at = utcnow() - timedelta(minutes=1)
        await record(
            s,
            user.id,
            FavoriteTarget(ref="dz:1", target_data={"artist": "A", "title": "T"}),
            True,
            ts=at,
            event_id="server",
        )
        await record_events(
            s, user.id, [dict(event_id="legacy", ts=at + timedelta(seconds=1), kind="like", ref="dz:1", client="pwa")]
        )
        p = await build_profile(s, user, now=at + timedelta(seconds=2))
        assert p.tracks["dz:1"].liked and p.tracks["dz:1"].score == pytest.approx(1, abs=0.001)
        await record(s, user.id, FavoriteTarget(ref="dz:1"), False, ts=at + timedelta(seconds=3))
        p = await build_profile(s, user, now=at + timedelta(seconds=4))
        assert "dz:1" not in p.tracks
        await record(s, user.id, FavoriteTarget(ref="dz:1"), True, ts=at + timedelta(seconds=5))
        p = await build_profile(s, user, now=at + timedelta(seconds=6))
        assert p.tracks["dz:1"].score == pytest.approx(1, abs=0.001)


@pytest.mark.parametrize("stage", ["queued", "downloading"])
async def test_pending_unlike_preserves_explicit_download_and_survives_stale_job(ledger, stage):
    from app.db import session_scope
    from app.favorites import download_requested
    from app.jobs.downloads import update_item
    from app.models import DownloadBatch, DownloadItem

    user, other = ledger
    async with session_scope() as s:
        batches = [DownloadBatch(user_id=u.id, origin="catalog") for u in (user, other)]
        s.add_all(batches)
        await s.flush()
        items = [
            DownloadItem(
                user_id=u.id,
                batch_id=b.id,
                status=stage,
                query_json={"query": {}, "meta": {"track_ref": "dz:pending"}, "star": True, "requested": True},
            )
            for u, b in zip((user, other), batches, strict=True)
        ]
        s.add_all(items)
        await s.flush()
        for item in items:
            await download_requested(s, item)
        stale = dict(items[0].query_json)
        ids = [i.id for i in items]
    async with session_scope() as s:
        await command(s, user, [FavoriteTarget(ref="dz:pending")], liked=False, nd=AsyncMock())
    await update_item(ids[0], query_json=stale)
    async with session_scope() as s:
        mine, theirs = [await s.get(DownloadItem, id_) for id_ in ids]
        assert mine is not None and theirs is not None
        assert mine.status == stage and not mine.query_json["star"] and mine.query_json["star_cancelled"]
        assert theirs.status == stage and theirs.query_json["star"]
        assert not next(iter((await snapshot(s, user.id)).values())).liked
        assert next(iter((await snapshot(s, other.id)).values())).liked


@pytest.mark.parametrize("typ", ["track", "album", "artist"])
@pytest.mark.parametrize("reverse", [False, True])
def test_retracting_compensation_restores_original_date_and_ignores_legacy_duplicate(typ, reverse):
    rows = [
        event("a", "like", typ=typ),
        event("legacy", "like", NOW + timedelta(seconds=1), typ=typ),
        event("c", "unlike", NOW + timedelta(seconds=2), typ=typ, reverts="a"),
        event("r", "like", NOW + timedelta(seconds=3), typ=typ, reverts="c"),
    ]
    state = next(iter(fold(list(reversed(rows)) if reverse else [*rows, rows[-1]]).values()))
    assert state.liked and state.since == NOW and state.event.event_id == "a"


@pytest.mark.parametrize("kind", ["like", "unlike"])
def test_late_retraction_preserves_newer_independent_decision(kind):
    rows = [
        event("a", "like"),
        event("c", "unlike", NOW + timedelta(seconds=1), reverts="a"),
        event("new", kind, NOW + timedelta(seconds=2)),
        event("r", "like", NOW + timedelta(seconds=3), reverts="c"),
    ]
    state = next(iter(fold(rows).values()))
    assert state.event.event_id == "new" and state.liked == (kind == "like")
    assert state.since == (rows[2].ts if kind == "like" else None)


def test_parallel_compensations_require_both_retractions():
    rows = [
        event("a", "like"),
        event("c1", "unlike", reverts="a"),
        event("c2", "unlike", reverts="a"),
        event("r1", "like", reverts="c1"),
    ]
    assert not next(iter(fold(rows).values())).liked
    rows.append(event("r2", "like", reverts="c2"))
    assert next(iter(fold(rows).values())).since == NOW


@pytest.mark.parametrize("bad", ["unknown", "target", "type", "user", "cycle"])
def test_inert_compensation_does_not_renew_grace_or_undo_unlike(bad):
    rows = [event("a", "unlike")]
    kwargs = {"reverts": "a", "received": NOW + timedelta(days=2)}
    if bad == "unknown":
        kwargs["reverts"] = "missing"
    elif bad == "target":
        kwargs["ref"] = "dz:2"
    elif bad == "type":
        kwargs["typ"] = "album"
    elif bad == "user":
        kwargs["user"] = 2
    else:
        kwargs["reverts"] = "other"
        rows.append(event("other", "unlike", reverts="bad"))
    rows.append(event("bad", "like", **kwargs))
    state = fold(rows)[("track", "ref:dz:1")]
    assert not state.liked and state.received_at == NOW


def test_long_compensation_chain_is_iterative_and_preserves_original_date():
    rows = [event("a", "like")]
    for i in range(2000):
        rows.append(event(f"c{i}", "unlike", reverts=rows[-1].event_id))
    assert next(iter(fold(rows).values())).since == NOW
