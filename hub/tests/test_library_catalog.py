"""Hub catalog: lazy artists, public uids with the legacy int lookup, sorts, covers, reconcile and bootstrap import.

Tracks are made by `import_file` from a tiny WAV, so no ffmpeg is needed; the catalog endpoints and jobs read only
the database and blobs.
"""

from __future__ import annotations

import asyncio
import math
import struct
import wave
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app import db
from app.auth import COOKIE
from app.config import get_settings
from app.library import editor
from app.library.artists import artist_name_norm
from app.library.catalog import catalog_commit
from app.library.importer import ImportSpec, import_file
from app.library.reconcile import MISSING_BLOB, MISSING_COVER, catalog_reconcile
from app.models import Album, Artist, Blob, DownloadBatch, DownloadItem, PlayEvent, Track, User, utcnow
from app.storage import backend, blobs
from app.tokens import issue_access
from tests.support import app_client


def make_wav(path: Path, seconds: float = 0.1, rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n))
        )
    return path


@pytest.fixture
async def api(hub_env, db_url) -> AsyncIterator:
    from app.main import create_app

    db.init_engine(db_url)
    async with db.session_scope() as s:
        user = User(username="admin", is_admin=True)
        s.add(user)
        await s.flush()
        token = issue_access(user)
    await db.dispose()  # the lifespan migrates/opens DATABASE_URL (this worker's clone)
    app = create_app()
    async with app.router.lifespan_context(app):
        async with app_client(app, login=None) as c:
            c.cookies.set(COOKIE, token)
            yield c


async def _import(tmp_path: Path, artist: str, title: str, album: str | None = None) -> tuple[int, str, str]:
    src = make_wav(tmp_path / f"{artist}-{title}.wav")
    async with db.session_scope() as s:
        track = await import_file(
            s,
            src,
            ImportSpec(artist=artist, title=title, album=album or title, album_artist=artist),
        )
        album_row = await s.get(Album, track.album_id)
        assert album_row is not None
        return track.id, track.uid, album_row.uid


async def test_lazy_artist_created_and_reused(hub_env, tmp_path, db_url):
    db.init_engine(db_url)
    await _import(tmp_path, "Кино", "Группа крови")
    await _import(tmp_path, "Кино", "Звезда по имени Солнце")
    async with db.session_scope() as s:
        artists = list((await s.execute(select(Artist))).scalars())
        albums = list((await s.execute(select(Album))).scalars())
    assert len(artists) == 1
    assert {a.artist_id for a in albums} == {artists[0].id}
    # case and ё are folded by the normalization: one entity, not two
    assert artist_name_norm("кино") == artist_name_norm("Кино") == artists[0].name_norm
    assert artists[0].name_norm != ""


async def test_parallel_artist_creation_makes_one_row(hub_env, tmp_path, db_url):
    db.init_engine(db_url)
    src = make_wav(tmp_path / "one.wav")

    async def create_one(i: int) -> None:
        async with db.session_scope() as s:
            await import_file(s, src, ImportSpec(artist="Однофамилец", title=f"Трек {i}", album="Общий"))
            await s.commit()

    await asyncio.gather(create_one(0), create_one(1))
    async with db.session_scope() as s:
        artists = list((await s.execute(select(Artist))).scalars())
        albums = list((await s.execute(select(Album))).scalars())
    assert len(artists) == 1
    assert {a.artist_id for a in albums} == {artists[0].id}


async def test_artist_merge_moves_albums_and_tracks(api, tmp_path):
    from app import db as d

    await _import(tmp_path, "Источник", "Альбом 1")
    await _import(tmp_path, "Источник", "Альбом 2")
    await _import(tmp_path, "Цель", "Альбом 3")
    async with d.session_scope() as s:
        source = (await s.execute(select(Artist).where(Artist.name == "Источник"))).scalar_one()
        target = (await s.execute(select(Artist).where(Artist.name == "Цель"))).scalar_one()
        source_uid, target_uid = source.uid, target.uid
    r = await api.post(f"/api/library/artists/{source_uid}/merge", json={"target_uid": target_uid})
    assert r.status_code == 200, r.text
    assert r.json()["artist"]["uid"] == target_uid
    async with d.session_scope() as s:
        assert (await s.execute(select(Artist).where(Artist.uid == source_uid))).scalar_one_or_none() is None
        albums = list((await s.execute(select(Album))).scalars())
        tracks = list((await s.execute(select(Track))).scalars())
    assert {a.artist_id for a in albums} == {target.id}
    assert all(t.artist == "Цель" for t in tracks)


async def test_uid_and_legacy_int_lookup(api, tmp_path):
    track_id, track_uid, album_uid = await _import(tmp_path, "Кино", "Группа крови")
    by_uid = await api.get(f"/api/library/albums/{album_uid}")
    assert by_uid.status_code == 200, by_uid.text
    album = by_uid.json()
    assert album["uid"] == album_uid and album["id"] is not None
    assert album["artist_uid"]
    assert album["tracks"][0]["uid"] == track_uid
    assert album["tracks"][0]["album_uid"] == album_uid
    assert album["tracks"][0]["album_id"] == album["id"]  # deprecated alias until stage 7
    by_int = await api.get(f"/api/library/albums/{album['id']}")
    assert by_int.status_code == 200 and by_int.json()["uid"] == album_uid
    track = await api.get(f"/api/library/tracks/{track_uid}")
    assert track.status_code == 200 and track.json()["uid"] == track_uid
    assert (await api.get(f"/api/library/tracks/{track_id}")).json()["uid"] == track_uid
    artist_uid = album["artist_uid"]
    page = await api.get(f"/api/library/artists/{artist_uid}")
    assert page.status_code == 200 and page.json()["artist"]["uid"] == artist_uid
    assert (await api.get("/api/library/albums/00000000-0000-0000-0000-000000000000")).status_code == 404
    assert (await api.get("/api/library/artists/00000000-0000-0000-0000-000000000000")).status_code == 404
    assert (await api.get("/api/library/tracks/999999")).status_code == 404


async def test_album_sorts_pagination_and_deprecated_type(api, tmp_path):
    for artist, title in (("Б", "Яблоко"), ("А", "Банан"), ("В", "Апельсин")):
        await _import(tmp_path, artist, title)
    names = [a["title"] for a in (await api.get("/api/library/albums?sort=alphabeticalByName")).json()]
    assert names == sorted(names)
    by_artist = [a["album_artist"] for a in (await api.get("/api/library/albums?sort=alphabeticalByArtist")).json()]
    assert by_artist == sorted(by_artist)
    assert len((await api.get("/api/library/albums?sort=random")).json()) == 3
    page = (await api.get("/api/library/albums?sort=alphabeticalByName&offset=1&limit=1")).json()
    assert len(page) == 1 and page[0]["title"] == names[1]
    # deprecated `type` is accepted as an alias of `sort`
    assert (await api.get("/api/library/albums?type=newest")).status_code == 200
    assert (await api.get("/api/library/albums?sort=nope")).status_code == 422
    assert (await api.get("/api/library/albums?limit=201")).status_code == 422


async def test_home_recent_frequent_starred(api, tmp_path):
    track_id, track_uid, album_uid = await _import(tmp_path, "Кино", "Группа крови")
    async with db.session_scope() as s:
        album = (await s.execute(select(Album).where(Album.uid == album_uid))).scalar_one()
        album.deezer_album_id = "10"
        track = await s.get(Track, track_id)
        assert track is not None
        user_id = (await s.execute(select(User.id))).scalar_one()
        now = datetime.now(UTC)
        s.add(PlayEvent(user_id=user_id, event_id="e1", ts=now, kind="start", track_uid=track_uid, client="pwa"))
        s.add(
            PlayEvent(
                user_id=user_id,
                event_id="e2",
                ts=now,
                kind="like",
                target_type="album",
                ref="dz:10",
                client="pwa",
            )
        )
    home = (await api.get("/api/library/home")).json()
    assert home["recent"][0]["uid"] == album_uid
    assert home["frequent"][0]["uid"] == album_uid
    assert home["starred"][0]["uid"] == album_uid
    assert home["artists"][0]["name"] == "Кино"
    starred = (await api.get("/api/library/albums?sort=starred")).json()
    assert [a["uid"] for a in starred] == [album_uid]


async def test_cover_by_uid_resize_and_404(api, tmp_path):
    import io

    from PIL import Image

    _track_id, _track_uid, album_uid = await _import(tmp_path, "Кино", "Группа крови")
    assert (await api.get(f"/api/library/albums/{album_uid}/cover")).status_code == 404
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, "JPEG")
    r = await api.put(f"/api/library/albums/{album_uid}/cover", files={"file": ("c.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["has_cover"] is True
    full = await api.get(f"/api/library/albums/{album_uid}/cover")
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
    # the cover is cacheable by the client for an hour: the URL carries the album uid, so it is stable
    assert "max-age=3600" in full.headers["cache-control"]
    small = await api.get(f"/api/library/albums/{album_uid}/cover?size=32")
    assert small.status_code == 200 and "max-age=3600" in small.headers["cache-control"]
    with Image.open(io.BytesIO(small.content)) as im:
        assert max(im.size) <= 32
    assert (await api.get("/api/library/albums/00000000-0000-0000-0000-000000000000/cover")).status_code == 404


async def test_edits_keep_uid(hub_env, tmp_path, db_url):
    import uuid

    db.init_engine(db_url)
    track_id, track_uid, album_uid = await _import(tmp_path, "Кино", "Группа крови")
    async with db.session_scope() as s:
        track = await s.get(Track, track_id)
        assert track is not None
        original_path = track.path
    # renaming the album moves the logical path but keeps the public id
    async with db.session_scope() as s:
        album = (await s.execute(select(Album).where(Album.uid == album_uid))).scalar_one()
        await editor.update_album(s, album, title="Новый альбом")
        track = await s.get(Track, track_id)
        assert track is not None
        assert track.uid == track_uid
        assert track.path != original_path and "Новый альбом" in (track.path or "")
        renamed_path = track.path
    # moving the track to another album does the same, without a re-import
    async with db.session_scope() as s:
        target = Album(uid=str(uuid.uuid4()), title="Другой", album_artist="Кино")
        s.add(target)
        await s.flush()
        await editor.move_tracks(s, [track_id], target)
        track = await s.get(Track, track_id)
        assert track is not None
        assert track.uid == track_uid
        assert track.path != renamed_path and track.album == "Другой"


async def test_catalog_commit_is_idempotent(hub_env, tmp_path, db_url):
    db.init_engine(db_url)
    track_id, track_uid, _album_uid = await _import(tmp_path, "Кино", "Группа крови")
    async with db.session_scope() as s:
        user = User(username="admin", is_admin=True)
        s.add(user)
        await s.flush()
        user_id = user.id
        batch = DownloadBatch(user_id=user_id, origin="search")
        s.add(batch)
        await s.flush()
        item = DownloadItem(batch_id=batch.id, user_id=user_id, query_json={"query": {"artist": "Кино", "title": "Т"}})
        s.add(item)
        await s.flush()
        item_id = item.id
    async with db.session_scope() as s:
        item = await s.get(DownloadItem, item_id)
        track = await s.get(Track, track_id)
        assert item is not None and track is not None
        first = await catalog_commit(s, item, track)
        assert first["uid"] == track_uid
    async with db.session_scope() as s:
        item = await s.get(DownloadItem, item_id)
        track = await s.get(Track, track_id)
        assert item is not None and track is not None
        second = await catalog_commit(s, item, track)
    assert second["idempotent"] is True
    assert item.status == "done" and item.track_id == track_id and item.blob_sha256
    assert item.query_json["uid"] == track_uid and item.query_json["library_track_id"] == track_id


async def test_catalog_commit_refuses_a_track_without_blob(hub_env, tmp_path, db_url):
    db.init_engine(db_url)
    async with db.session_scope() as s:
        user = User(username="u", is_admin=False)
        s.add(user)
        await s.flush()
        batch = DownloadBatch(user_id=user.id, origin="search")
        s.add(batch)
        await s.flush()
        item = DownloadItem(batch_id=batch.id, user_id=user.id, query_json={})
        track = Track(
            uid="00000000-0000-0000-0000-0000000000aa",
            artist="А",
            title="Т",
            fingerprint="а|т",
            acquired_at=utcnow(),
        )
        s.add_all([item, track])
        await s.flush()
        with pytest.raises(ValueError, match="без блоба"):
            await catalog_commit(s, item, track)
        assert item.status == "queued" and item.track_id is None


async def test_reconcile_marks_and_clears_integrity(hub_env, tmp_path, db_url):
    db.init_engine(db_url)
    track_id, _track_uid, album_uid = await _import(tmp_path, "Кино", "Группа крови")
    async with db.session_scope() as s:
        track = await s.get(Track, track_id)
        assert track is not None and track.blob_id is not None
        blob = await s.get(Blob, track.blob_id)
        assert blob is not None
        await backend().delete(blobs.object_key(blob.sha256, blob.kind))
    async with db.session_scope() as s:
        result = await catalog_reconcile(s)
        track = await s.get(Track, track_id)
        album = (await s.execute(select(Album).where(Album.uid == album_uid))).scalar_one()
    assert result["missing_blob"] == 1
    assert track is not None and track.integrity_issue == MISSING_BLOB
    assert album.integrity_issue == MISSING_COVER
    # the object comes back: the next pass clears the markers
    async with db.session_scope() as s:
        track = await s.get(Track, track_id)
        assert track is not None and track.blob_id is not None
        blob = await s.get(Blob, track.blob_id)
        assert blob is not None
    await backend().put(blobs.object_key(blob.sha256, blob.kind), b"restored")
    async with db.session_scope() as s:
        track = await s.get(Track, track_id)
        assert track is not None and track.blob_id is not None
        blob_row = await s.get(Blob, track.blob_id)
        assert blob_row is not None
        await backend().put(blobs.object_key(blob_row.sha256, blob_row.kind), b"restored")
        album = (await s.execute(select(Album).where(Album.uid == album_uid))).scalar_one()
        album.cover_blob_id = None  # no cover: the album's marker is cleared only when a cover exists
        result = await catalog_reconcile(s)
        track = await s.get(Track, track_id)
        await s.refresh(album)
    assert result["cleared_missing_blob"] == 1
    assert track is not None and track.integrity_issue is None


async def test_reconcile_reports_orphan_blob_and_does_not_index_bucket(api, tmp_path):
    await _import(tmp_path, "Кино", "Группа крови")
    async with db.session_scope() as s:
        orphan = await blobs.store(s, b"cover-bytes", kind=blobs.COVER_KIND, content_type="image/jpeg")
        await s.commit()
    # an object that appeared in the bucket outside the Hub
    await backend().put(f"audio/{'a' * 64}", b"stray")
    async with db.session_scope() as s:
        before = len(list((await s.execute(select(Track))).scalars()))
        result = await catalog_reconcile(s)
        after = len(list((await s.execute(select(Track))).scalars()))
    assert result["orphan_blob"] >= 1
    assert before == after
    assert (await api.get("/healthz")).json()["catalog"]["orphan_blob"] >= 1
    async with db.session_scope() as s:
        assert (await s.execute(select(Blob).where(Blob.id == orphan.id))).scalar_one().status == blobs.COMMITTED


async def test_bootstrap_import_uses_the_shared_importer(api, tmp_path, monkeypatch):
    from app.library import probe, tagger

    class Info:
        duration_s = 1.0
        bitrate_kbps = 128
        codec = "pcm_s16le"

    async def fake_probe(_path):
        return Info()

    monkeypatch.setattr(probe, "probe", fake_probe)
    monkeypatch.setattr(tagger, "read_tags", lambda _p: {"artist": "Кино", "title": "Группа крови", "album": "Кино"})
    monkeypatch.setattr(tagger, "read_cover", lambda _p: None)

    root = get_settings().music_root
    mounted = root / "mounted"
    mounted.mkdir(parents=True, exist_ok=True)
    make_wav(mounted / "one.wav")
    make_wav(mounted / "two.wav")
    (mounted / "bad.txt").write_text("nope")
    r = await api.post(
        "/api/library/import",
        json={"paths": ["mounted/one.wav", "mounted/two.wav", "mounted/bad.txt", "../outside.wav"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 2
    assert any("bad.txt" in x for x in body["failed"])
    assert any("вне каталога" in x for x in body["failed"])
    stored = await api.get("/api/library/albums")
    assert len(stored.json()) == 1  # the same album for both files
    assert (await api.get("/api/library/albums")).json()[0]["track_count"] == 2
