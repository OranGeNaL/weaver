"""Postgres persistence contracts: JSONB paths, upserts, search_text, timestamptz and URL guards.

Each test is written so that reverting the corresponding change makes it fail:
- `->>`/`?` and the int casts — the JSON path tests;
- `dialects.postgresql.insert` — the upsert test (only Postgres has `ON CONFLICT`);
- `search_text` + `ILIKE ALL` — the search tests;
- timestamptz — the round-trip test;
- DATABASE_URL/TEST_DATABASE_URL guards — the last test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import db
from app.library.search import album_search_text, ilike_all, track_search_text
from app.models import Album, CatalogCache, DownloadBatch, DownloadItem, Job, PlayEvent, Track, User, utcnow


def _album(**fields) -> Album:
    return Album(uid=str(uuid.uuid4()), **fields)


def _track(**fields) -> Track:
    defaults = {"uid": str(uuid.uuid4()), "path": f"{uuid.uuid4().hex}.mp3", "fingerprint": "x|y"}
    return Track(**defaults | fields)


async def test_jsonb_numeric_paths_use_explicit_casts(db_url):
    db.init_engine(db_url)
    try:
        async with db.session_scope() as s:
            user = User(username="jsonb", nd_username="jsonb", nd_password_enc="x")
            s.add(user)
            await s.flush()
            s.add(Job(kind="mix_build", payload_json={"user_id": user.id, "mix_id": 9}))
            batch = DownloadBatch(user_id=user.id, origin="catalog")
            s.add(batch)
            await s.flush()
            s.add(
                DownloadItem(
                    user_id=user.id,
                    batch_id=batch.id,
                    query_json={"import": {"id": 3, "album": 4}, "mix": {"id": 9}, "meta": {"track_ref": "dz:1"}},
                    status="queued",
                )
            )
            await s.flush()
        async with db.session_scope() as s:
            job = (
                await s.execute(select(Job).where(cast(Job.payload_json["user_id"].astext, Integer) == user.id))
            ).scalar_one()
            assert job.payload_json["mix_id"] == 9
            item = (
                await s.execute(
                    select(DownloadItem).where(cast(DownloadItem.query_json["import"]["album"].astext, Integer) == 4)
                )
            ).scalar_one()
            assert item.query_json["meta"]["track_ref"] == "dz:1"
            assert (
                await s.execute(
                    select(DownloadItem).where(cast(DownloadItem.query_json["mix"]["id"].astext, Integer) == 9)
                )
            ).scalar_one().id == item.id
    finally:
        await db.dispose()


async def test_jsonb_distinguishes_json_null_from_a_missing_key(db_url):
    db.init_engine(db_url)
    try:
        async with db.session_scope() as s:
            s.add(Job(kind="a", payload_json={"present": None}))
            s.add(Job(kind="b", payload_json={}))
            await s.flush()
        async with db.session_scope() as s:
            # `->>` reads JSON null and a missing key alike; `?` is the operator that tells them apart
            same = (
                await s.execute(
                    select(func.count()).select_from(Job).where(Job.payload_json["present"].astext.is_(None))
                )
            ).scalar_one()
            json_null = (
                await s.execute(select(func.count()).select_from(Job).where(Job.payload_json.has_key("present")))
            ).scalar_one()
            assert same == 2 and json_null == 1
    finally:
        await db.dispose()


async def test_upsert_through_postgresql_insert_is_idempotent(db_url):
    db.init_engine(db_url)
    try:
        now = utcnow()
        statement = (
            pg_insert(CatalogCache)
            .values(key="cache-key", payload_json={"v": 1}, ttl_s=60, fetched_at=now)
            .on_conflict_do_nothing()
            .returning(CatalogCache.key)
        )
        async with db.session_scope() as session:
            first = (await session.execute(statement)).first()
            second = (await session.execute(statement)).first()
            assert first is not None and second is None
        async with db.session_scope() as s:
            assert (await s.execute(select(func.count()).select_from(CatalogCache))).scalar_one() == 1
    finally:
        await db.dispose()


async def test_search_text_is_case_insensitive_multitoken_and_follows_tags(db_url):
    db.init_engine(db_url)
    try:
        async with db.session_scope() as s:
            kino = _album(title="Группа крови", album_artist="Кино")
            s.add(kino)
            await s.flush()
            s.add(_track(album_id=kino.id, artist="Кино", title="Группа крови", album="Группа крови"))
            s.add(_track(artist="Ёлка", title="Прованс", album="Точки расставлены"))
            await s.flush()
        async with db.session_scope() as s:
            rows = (
                (await s.execute(select(Track.title).where(Track.search_text.ilike(ilike_all("КИНО"))))).scalars().all()
            )
            assert rows == ["Группа крови"]
            assert (
                await s.execute(select(Track.title).where(Track.search_text.ilike(ilike_all("ёлка!"))))
            ).scalars().all() == ["Прованс"]
            assert (
                await s.execute(select(Track.title).where(Track.search_text.ilike(ilike_all("группа крови"))))
            ).scalars().all() == ["Группа крови"]
            assert (
                await s.execute(select(Album.title).where(Album.search_text.ilike(ilike_all("кино"))))
            ).scalars().all() == ["Группа крови"]
            assert ilike_all("") is None and ilike_all("%") is None  # nothing to search for: no match-all
            # the album's search_text follows a tag edit
            album = (await s.execute(select(Album))).scalar_one()
            album.title = "Звезда по имени Солнце"
            await s.flush()
            assert album.search_text == album_search_text(album.album_artist, "Звезда по имени Солнце")
            track = (await s.execute(select(Track).where(Track.artist == "Кино"))).scalar_one()
            track.title = "Звезда по имени Солнце"
            await s.flush()
            assert track.search_text == track_search_text(track.artist, track.title, track.album)
    finally:
        await db.dispose()


async def test_timestamps_round_trip_as_timezone_aware_utc(db_url):
    db.init_engine(db_url)
    try:
        moment = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
        async with db.session_scope() as s:
            user = User(username="tz", nd_username="tz", nd_password_enc="x")
            s.add(user)
            await s.flush()
            s.add(PlayEvent(user_id=user.id, event_id="e1", ts=moment, kind="finish", client="pwa", received_at=moment))
        async with db.session_scope() as s:
            event = (await s.execute(select(PlayEvent))).scalar_one()
            assert event.ts == moment and event.ts.tzinfo is not None
            assert event.received_at is not None and event.received_at.utcoffset() == timedelta(0)
    finally:
        await db.dispose()


def test_database_url_guards_reject_missing_sqlite_and_equal_test_url(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        db.database_url()

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/hub.sqlite3")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="SQLite"):
        db.database_url()

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/hub")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/hub")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="совпадает"):
        db.test_database_url()
    get_settings.cache_clear()


async def test_connect_retry_fails_with_a_clear_error_when_postgres_is_unreachable():
    db.init_engine("postgresql+asyncpg://hub:hub@127.0.0.1:1/none")
    try:
        with pytest.raises(RuntimeError, match="Не удалось подключиться к Postgres"):
            await db.connect_with_retry(attempts=2, base_delay_s=0)
    finally:
        await db.dispose()
