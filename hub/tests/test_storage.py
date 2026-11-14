"""Storage layer: backend contract, immutable blobs, deduplication, hot cache, trash/GC, blob endpoint.

The fs backend is the dev/test one; a real S3 smoke test is a separate `s3`-marked test (HUB_S3_TESTS=1).
"""

from __future__ import annotations

import math
import struct
import uuid
import wave
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import db
from app.config import get_settings
from app.library import editor
from app.library.importer import ImportSpec, import_file
from app.models import Blob, Track
from app.storage import ObjectNotFound, PresignUnavailable, backend, blobs, cache, reset
from app.storage.cache import HotCache
from app.storage.local import LocalBackend
from tests.support import app_client


def make_wav(path: Path, seconds: float = 0.4, rate: int = 8000) -> Path:
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


async def test_local_backend_contract(tmp_path):
    b = LocalBackend(tmp_path / "objects")
    info = await b.put("audio/abc", b"0123456789", content_type="audio/wav", metadata={"sha256": "abc"})
    assert info.size == 10
    head = await b.head("audio/abc")
    assert head is not None and head.size == 10 and head.metadata["sha256"] == "abc"
    assert await b.get("audio/abc") == b"0123456789"
    assert await b.get_range("audio/abc", 2, 4) == b"234"
    chunks = [c async for c in b.iter_range("audio/abc", 5, 9)]
    assert b"".join(chunks) == b"56789"
    await b.copy("audio/abc", "audio/def")
    assert await b.get("audio/def") == b"0123456789"
    assert await b.list("audio/") == ["audio/abc", "audio/def"]
    assert await b.head("audio/missing") is None
    with pytest.raises(PresignUnavailable):
        await b.presign_get("audio/abc")
    await b.delete("audio/abc")
    assert await b.head("audio/abc") is None


async def test_bad_object_key_is_rejected(tmp_path):
    b = LocalBackend(tmp_path / "objects")
    with pytest.raises(ValueError):
        b.path_of("../secret")
    with pytest.raises(ValueError):
        b.path_of("a/../../secret")


async def test_two_imports_of_one_file_share_a_blob(hub_env, db_url):
    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "two.wav")
    async with db.session_scope() as s:
        first = await import_file(
            s, src, ImportSpec(artist="Кино", title="Группа крови", album="Группа крови", album_artist="Кино")
        )
        second = await import_file(
            s, src, ImportSpec(artist="Кино", title="Группа крови (2)", album="Группа крови", album_artist="Кино")
        )
        sha = first.blob_id
        assert first.blob_id == second.blob_id
        assert first.uid != second.uid
        assert first.path != second.path  # logical paths differ, the bytes are shared
    async with db.session_scope() as s:
        blobs_rows = (await s.execute(select(Blob))).scalars().all()
        assert len(blobs_rows) == 1
        blob = blobs_rows[0]
        assert blob.id == sha and blob.status == blobs.COMMITTED and blob.verified_at is not None
        # both tracks point at the one blob; one manifest per uid
        tracks = (await s.execute(select(Track))).scalars().all()
        assert {t.blob_id for t in tracks} == {blob.id}
        for t in tracks:
            assert await blobs.read_manifest(blob.sha256, t.uid) is not None
    # the object is in the store, not in the old on-disk library
    b = backend()
    info = await b.head(f"audio/{blob.sha256}")
    assert info is not None and info.size == src.stat().st_size
    assert not list((get_settings().library_dir).rglob("*.wav"))
    assert (await b.list("pending/")) == []


async def test_repeated_store_is_idempotent_and_commits(hub_env, db_url):
    db.init_engine(db_url)
    data = b"same bytes"
    async with db.session_scope() as s:
        one = await blobs.store(s, data, content_type="audio/wav", metadata={"uid": "u1"})
    async with db.session_scope() as s:
        two = await blobs.store(s, data, content_type="audio/wav", metadata={"uid": "u2"})
        assert two.sha256 == one.sha256
        assert (await s.execute(select(func.count(Blob.id)))).scalar_one() == 1


async def test_hot_cache_promotes_only_a_complete_copy(tmp_path):
    b = LocalBackend(tmp_path / "objects")
    await b.put("audio/" + "a" * 64, b"0123456789")
    c = HotCache(tmp_path / "cache", max_bytes=10_000)
    path = await c.ensure(b, "a" * 64, expected_size=10)
    assert path.read_bytes() == b"0123456789"
    other = HotCache(tmp_path / "cache2", max_bytes=10_000)
    with pytest.raises(ObjectNotFound):
        await other.ensure(b, "a" * 64, expected_size=11)  # head says 10: a mismatch is never promoted
    assert not (tmp_path / "cache2" / ("a" * 64)).exists()
    assert list((tmp_path / "cache2").glob("*.part")) == []


async def test_hot_cache_evicts_least_recently_used(tmp_path):
    b = LocalBackend(tmp_path / "objects")
    c = HotCache(tmp_path / "cache", max_bytes=10)
    for name, data in (("a" * 64, b"12345"), ("b" * 64, b"67890"), ("c" * 64, b"abcde")):
        await b.put(f"audio/{name}", data)
        await c.ensure(b, name)
    # three 5-byte files against a 10-byte limit: the oldest goes
    assert c.cached("a" * 64) is None
    assert c.cached("b" * 64) is not None and c.cached("c" * 64) is not None


async def test_media_blob_endpoint_needs_the_service_token(hub_env, db_url):
    from app.main import create_app

    db.init_engine(db_url)
    payload = b"0123456789"
    async with db.session_scope() as s:
        blob = await blobs.store(s, payload, content_type="audio/wav")
    # the app lifespan runs migrations against DATABASE_URL (hub_env points it at this worker's clone)
    await db.dispose()
    app = create_app()
    async with app_client(app, login=None) as c:
        url = f"/api/media/blob/{blob.sha256}"
        assert (await c.get(url)).status_code == 401
        assert (await c.get(url, headers={"Authorization": "Bearer wrong"})).status_code == 401
        r = await c.get(url, headers={"Authorization": "Bearer test-blob-token", "Range": "bytes=2-4"})
        assert r.status_code == 206 and r.content == payload[2:5]
        assert r.headers["content-range"] == "bytes 2-4/10"


async def test_library_items_uses_a_url_for_the_embedder(hub_env, db_url):
    from app.mixes.build import library_items

    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "embed.wav")
    async with db.session_scope() as s:
        track = await import_file(s, src, ImportSpec(artist="А", title="Т", album="А"))
        track.nd_song_id = "nd42"
    items = await library_items()
    assert items and items[0]["key"].startswith("nd:")
    assert items[0]["url"].endswith(f"/api/media/blob/{await _sha_of(track.id)}")


async def _sha_of(track_id: int) -> str:
    async with db.session_scope() as s:
        t = await s.get(Track, track_id)
        assert t is not None and t.blob_id is not None
        blob = await s.get(Blob, t.blob_id)
        assert blob is not None
        return blob.sha256


async def test_trash_restore_and_gc_free_the_blob(hub_env, db_url):
    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "trash.wav")
    async with db.session_scope() as s:
        track = await import_file(s, src, ImportSpec(artist="А", title="Т", album="А"))
        track_id, blob_id = track.id, track.blob_id
        await editor.trash_track(s, track)
        assert track.deleted_at is not None
    async with db.session_scope() as s:
        t = await s.get(Track, track_id)
        assert t is not None
        await editor.restore_track(s, t)
        assert t.deleted_at is None
    # trash again, let the period expire and run GC: the row and then the object disappear
    async with db.session_scope() as s:
        t = await s.get(Track, track_id)
        assert t is not None
        await editor.trash_track(s, t)
        blob = await s.get(Blob, blob_id)
        assert blob is not None
        sha = blob.sha256
    from app.storage.gc import gc_tracks_and_blobs

    # cutoff and GC clock in the future: the trash period and the orphan grace period are over
    await gc_tracks_and_blobs(-1, now=__import__("time").time() + 2 * 86400)
    async with db.session_scope() as s:
        assert await s.get(Track, track_id) is None
        blob = await s.get(Blob, blob_id)
        assert blob is not None and blob.status == "deleted"
    assert await backend().head(f"audio/{sha}") is None


async def test_export_writes_tags_on_a_copy_only(hub_env, db_url):
    from app.library import export

    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "export.wav")
    async with db.session_scope() as s:
        track = await import_file(s, src, ImportSpec(artist="А", title="Т", album="А"))
        blob = await s.get(Blob, track.blob_id)
        assert blob is not None and blob.sha256 is not None
        original = await backend().get(f"audio/{blob.sha256}")
        out = await export.materialize(s, track)
        try:
            tags = await _read_tags(out)
            assert tags["title"] == "Т"
            # the immutable object did not change: tags live only on the exported copy
            assert await backend().get(f"audio/{blob.sha256}") == original
        finally:
            export.cleanup(out)


async def _read_tags(path: Path) -> dict:
    import asyncio

    from app.library import tagger

    return await asyncio.to_thread(tagger.read_tags, path)


def test_reset_clears_singletons(hub_env):
    a = backend()
    assert backend() is a
    reset()
    assert backend() is not a


@pytest.mark.s3
async def test_s3_backend_smoke():
    """Real S3/RustFS: put/head/get/range/list/delete/presign. Enabled with HUB_S3_TESTS=1."""
    import os

    from app.storage.s3 import S3Backend

    b = S3Backend(
        endpoint=os.environ.get("S3_ENDPOINT", ""),
        bucket=os.environ.get("S3_BUCKET", "homemusic"),
        region=os.environ.get("S3_REGION", "us-east-1"),
        access_key=os.environ.get("S3_ACCESS_KEY", ""),
        secret_key=os.environ.get("S3_SECRET_KEY", ""),
        path_style=os.environ.get("S3_PATH_STYLE", "true").lower() != "false",
    )
    await b.ensure_bucket()
    key = f"pending/smoke-{uuid.uuid4().hex}"
    await b.put(key, b"0123456789")
    try:
        assert await b.get(key) == b"0123456789"
        assert await b.get_range(key, 1, 3) == b"123"
        assert key in await b.list("pending/")
        assert (await b.presign_get(key)).startswith("http")
        await b.copy(key, key + "-copy")
        assert await b.get(key + "-copy") == b"0123456789"
        await b.delete(key + "-copy")
    finally:
        await b.delete(key)
    assert await b.head(key) is None
    assert cache is not None and blobs is not None


def test_fs_backend_is_refused_in_production():
    from app.config import Settings

    settings = Settings(storage_backend="fs", environment="production")
    with pytest.raises(RuntimeError, match="production"):
        settings.validate_storage()
    Settings(storage_backend="fs", environment="development").validate_storage()


async def test_health_reports_storage_without_failing(hub_env, db_url):
    from app.main import create_app

    db.init_engine(db_url)
    await db.dispose()
    app = create_app()
    async with app_client(app, login=None) as c:
        r = await c.get("/healthz")
        assert r.status_code == 200 and r.json()["ok"] is True
        assert r.json()["storage"]["backend"] == "fs"
        assert "cache_bytes" in r.json()["storage"]


async def test_metadata_edits_leave_the_blob_untouched(hub_env, db_url):
    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "edit.wav")
    async with db.session_scope() as s:
        track = await import_file(s, src, ImportSpec(artist="А", title="До", album="А"))
        blob = await s.get(Blob, track.blob_id)
        assert blob is not None and blob.sha256 is not None
        original = await backend().get(f"audio/{blob.sha256}")
        await editor.update_track(s, track, title="После")
        assert track.title == "После"
    assert await backend().get(f"audio/{blob.sha256}") == original


@pytest.mark.s3
async def test_s3_end_to_end_import_listen_cover_and_export(hub_env, db_url, monkeypatch):
    """The whole storage path on RustFS/S3: import -> bucket, stream by Range, cover, export."""
    import io
    import os

    from PIL import Image

    from app import config, listen, storage
    from app.library import covers, export

    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_ENDPOINT", os.environ.get("S3_ENDPOINT", ""))
    monkeypatch.setenv("S3_BUCKET", f"homemusic-test-{uuid.uuid4().hex[:8]}")
    monkeypatch.setenv("S3_ACCESS_KEY", os.environ.get("S3_ACCESS_KEY", ""))
    monkeypatch.setenv("S3_SECRET_KEY", os.environ.get("S3_SECRET_KEY", ""))
    config.get_settings.cache_clear()
    storage.reset()
    await storage.startup()
    db.init_engine(db_url)

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, "JPEG")
    src = make_wav(hub_env / "staging" / "s3.wav")
    async with db.session_scope() as s:
        track = await import_file(
            s,
            src,
            ImportSpec(artist="А", title="Т", album="А", cover=buf.getvalue()),
        )
        track.nd_song_id = "nd-s3"
        album_id = track.album_id
        assert track.blob_id is not None
        blob = await s.get(Blob, track.blob_id)
        assert blob is not None and blob.status == blobs.COMMITTED
        sha = blob.sha256
    assert await backend().head(f"audio/{sha}") is not None
    assert await backend().head(f"manifests/{sha}/{track.uid}.json") is not None

    media = await listen.open_media("nd:nd-s3", "bytes=0-9")
    if media.path is not None:
        data = media.path.read_bytes()[:10]
    else:
        assert media.body is not None
        data = b"".join([chunk async for chunk in media.body])[:10]
        if media.close is not None:
            await media.close()
    assert data == src.read_bytes()[:10]
    assert media.status in (200, 206)

    from app.models import Album

    async with db.session_scope() as s:
        album = await s.get(Album, album_id)
        assert album is not None
        found = await covers.cached_or_resized(s, album, 16)
        assert found is not None and len(found) > 0
        row = await s.get(Track, track.id)
        assert row is not None
        out = await export.materialize(s, row)
        try:
            assert out.read_bytes()[:4] == b"RIFF"
        finally:
            export.cleanup(out)


async def test_album_metadata_edit_keeps_paths_and_nd_ids(hub_env, db_url):
    from app.models import Album

    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "meta.wav")
    async with db.session_scope() as s:
        track = await import_file(s, src, ImportSpec(artist="А", title="Т", album="А"))
        track.nd_song_id = "nd-meta"
        album = await s.get(Album, track.album_id)
        assert album is not None
        before = track.path
        await editor.update_album(s, album, genre="Rock")
        assert track.path == before
        assert track.nd_song_id == "nd-meta"


async def test_library_etag_is_the_sha_on_cache_hit_and_miss(hub_env, db_url):
    from app import listen

    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "etag.wav")
    async with db.session_scope() as s:
        track = await import_file(s, src, ImportSpec(artist="А", title="Т", album="А"))
        track.nd_song_id = "nd-etag"
        blob = await s.get(Blob, track.blob_id)
        assert blob is not None
        sha = blob.sha256
    first = await listen.open_media("nd:nd-etag", "bytes=0-9")
    if first.body is not None:
        b"".join([c async for c in first.body])
        if first.close is not None:
            await first.close()
    await cache().ensure(backend(), sha, expected_size=first.choice.blob_size)  # the miss filled the cache
    second = await listen.open_media("nd:nd-etag", "bytes=0-9")
    assert second.path is not None
    assert first.headers.get("etag") == sha and second.headers.get("etag") == sha


async def test_storage_startup_survives_an_unavailable_bucket(hub_env, monkeypatch):
    from app import config, storage
    from app.storage.s3 import S3Backend

    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("S3_BUCKET", "unreachable")
    config.get_settings.cache_clear()
    storage.reset()

    async def boom(self):
        raise OSError("bucket is down")

    async def down(self):
        return False

    monkeypatch.setattr(S3Backend, "ensure_bucket", boom)
    monkeypatch.setattr(S3Backend, "check", down)
    await storage.startup()  # degraded storage must not stop the Hub
    assert (await storage.status())["available"] is False


async def test_import_stats_report_staging_free_and_bucket_bytes(hub_env, db_url):
    from app.imports.core import _bucket_bytes, _free_bytes

    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "stats.wav")
    free = _free_bytes()
    assert free is not None and free > 0
    async with db.session_scope() as session:
        track = await import_file(session, src, ImportSpec(artist="А", title="Т", album="А"))
        blob = await session.get(Blob, track.blob_id)
        assert blob is not None
        expected = blob.size
    assert await _bucket_bytes() == expected


async def test_gc_keeps_a_blob_shared_by_another_track(hub_env, db_url):
    from app.storage.gc import gc

    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "shared.wav")
    async with db.session_scope() as session:
        one = await import_file(session, src, ImportSpec(artist="А", title="Один", album="А"))
        two = await import_file(session, src, ImportSpec(artist="А", title="Два", album="А"))
        assert one.blob_id == two.blob_id
        blob = await session.get(Blob, one.blob_id)
        assert blob is not None
        sha = blob.sha256
        blob_id = blob.id
        two_id = two.id
        await editor.trash_track(session, one)
    async with db.session_scope() as session:
        await gc(session, now=__import__("time").time() + 2 * 86400)
        assert await session.get(Track, two_id) is not None
        assert await session.get(Blob, blob_id) is not None
    assert await backend().head(f"audio/{sha}") is not None


async def test_gc_retries_a_failed_object_delete(hub_env, db_url):
    from app.storage.gc import gc

    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "gc-retry.wav")
    async with db.session_scope() as session:
        track = await import_file(session, src, ImportSpec(artist="А", title="Т", album="А"))
        blob = await session.get(Blob, track.blob_id)
        assert blob is not None
        blob_id, sha = blob.id, blob.sha256
        await session.delete(track)
    b = backend()
    original = b.delete

    async def boom(key: str) -> None:
        raise OSError("storage is down")

    b.delete = boom  # type: ignore[method-assign]
    try:
        async with db.session_scope() as session:
            await gc(session, now=__import__("time").time() + 2 * 86400)
            row = await session.get(Blob, blob_id)
            assert row is not None and row.status == "committed"  # not lost: the next GC retries
    finally:
        b.delete = original  # type: ignore[method-assign]
    assert await backend().head(f"audio/{sha}") is not None
    async with db.session_scope() as session:
        await gc(session, now=__import__("time").time() + 2 * 86400)
        row = await session.get(Blob, blob_id)
        assert row is not None and row.status == "deleted"
    assert await backend().head(f"audio/{sha}") is None


async def test_bucket_bytes_counts_only_committed_blobs(hub_env, db_url):
    from app.imports.core import _bucket_bytes
    from app.storage.gc import gc_tracks_and_blobs

    db.init_engine(db_url)
    src = make_wav(hub_env / "staging" / "bucket-bytes.wav")
    async with db.session_scope() as session:
        track = await import_file(session, src, ImportSpec(artist="А", title="Т", album="А"))
        track_id = track.id
        await editor.trash_track(session, track)
    assert await _bucket_bytes() == src.stat().st_size
    await gc_tracks_and_blobs(-1, now=__import__("time").time() + 2 * 86400)
    async with db.session_scope() as session:
        assert await session.get(Track, track_id) is None
    assert await _bucket_bytes() == 0


def test_malformed_range_serves_the_whole_file():
    from app import listen
    from app.api import media

    assert listen._parse_range("bytes=abc-def", 100) == (0, None, False)
    assert media._parse_range("bytes=abc-def", 100) == (0, None, False)


async def test_media_endpoint_full_get_returns_404_when_the_object_is_missing(hub_env, db_url):
    from app.main import create_app

    db.init_engine(db_url)
    async with db.session_scope() as session:
        blob = await blobs.store(session, b"gone-object-bytes", content_type="audio/wav")
        sha = blob.sha256
    await backend().delete(f"audio/{sha}")
    await db.dispose()
    app = create_app()
    async with app_client(app, login=None) as c:
        r = await c.get(f"/api/media/blob/{sha}", headers={"Authorization": "Bearer test-blob-token"})
        assert r.status_code == 404
