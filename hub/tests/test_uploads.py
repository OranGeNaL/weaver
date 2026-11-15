"""Uploads of own files: a commit with `playlist_id` adds the imported tracks to the user's Hub playlist.

WAV files are written by the `wave` module, so the test needs no ffmpeg: a missing ffprobe only drops the duration.
"""

from __future__ import annotations

import json
import math
import struct
import wave
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from tests.support import app_client


def make_wav(path: Path, seconds: float = 0.1, rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(frames))
        )
    return path


@pytest.fixture
async def api(hub_env) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        async with app_client(app) as client:
            yield client


async def upload(api: httpx.AsyncClient, tmp_path: Path, count: int) -> list[dict]:
    files = [
        ("files", (f"song{i}.wav", make_wav(tmp_path / f"song{i}.wav").read_bytes(), "audio/wav")) for i in range(count)
    ]
    r = await api.post("/api/uploads", files=files)
    assert r.status_code == 200, r.text
    return r.json()


async def playlist_titles(api: httpx.AsyncClient, uid: str) -> list[str]:
    r = await api.get(f"/api/playlists/{uid}")
    assert r.status_code == 200, r.text
    return [t["title"] for t in r.json()["tracks"]]


def commit_payload(uploads: list[dict], *, playlist_id: str | None = None, order: tuple[int, ...] = ()) -> dict:
    index = order or tuple(range(len(uploads)))
    return {
        "album": {"mode": "new", "title": "Мои файлы", "album_artist": "Я"},
        "tracks": [
            {"upload_id": uploads[i]["id"], "title": f"Трек {i}", "artist": "Я", "track_no": n + 1}
            for n, i in enumerate(index)
        ],
        "playlist_id": playlist_id,
    }


async def test_commit_with_playlist_adds_tracks_in_request_order(api, tmp_path):
    created = await api.post("/api/playlists", json={"name": "Мои загрузки"})
    assert created.status_code == 200, created.text
    playlist_uid = created.json()["id"]
    uploads = await upload(api, tmp_path, 3)

    payload = commit_payload(uploads, playlist_id=playlist_uid, order=(2, 0, 1))
    r = await api.post("/api/uploads/commit", data={"payload": json.dumps(payload)})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 3

    # the playlist follows `tracks`, not the order the files were uploaded in
    assert await playlist_titles(api, playlist_uid) == ["Трек 2", "Трек 0", "Трек 1"]


async def test_commit_without_playlist_leaves_playlists_alone(api, tmp_path):
    uploads = await upload(api, tmp_path, 1)
    r = await api.post("/api/uploads/commit", data={"payload": json.dumps(commit_payload(uploads))})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    assert (await api.get("/api/playlists")).json() == []


async def test_commit_with_unknown_playlist_keeps_the_uploads(api, tmp_path):
    uploads = await upload(api, tmp_path, 2)
    payload = commit_payload(uploads, playlist_id="нет-такого-плейлиста")
    r = await api.post("/api/uploads/commit", data={"payload": json.dumps(payload)})
    assert r.status_code == 404
    assert r.json()["detail"] == "Плейлист не найден"

    # nothing was imported: the files are still staged and can be committed again
    staged = (await api.get("/api/uploads?status=uploaded")).json()
    assert sorted(u["id"] for u in staged) == sorted(u["id"] for u in uploads)
    assert (await api.get("/api/library/albums")).json() == []


async def test_commit_with_foreign_playlist_is_not_owned(api, tmp_path):
    from app.db import session_scope
    from app.models import Playlist, User

    async with session_scope() as s:
        other = User(username="other")
        s.add(other)
        await s.flush()
        foreign = Playlist(uid="foreign-playlist", owner_id=other.id, name="Чужой")
        s.add(foreign)
        await s.flush()

    uploads = await upload(api, tmp_path, 1)
    payload = commit_payload(uploads, playlist_id=foreign.uid)
    r = await api.post("/api/uploads/commit", data={"payload": json.dumps(payload)})
    assert r.status_code == 404
    assert r.json()["detail"] == "Плейлист не найден"
    assert [u["id"] for u in (await api.get("/api/uploads?status=uploaded")).json()] == [uploads[0]["id"]]


async def test_commit_adds_only_the_imported_tracks_of_the_request(api, tmp_path):
    from app.config import get_settings
    from app.db import session_scope
    from app.models import Upload

    created = await api.post("/api/playlists", json={"name": "Мои загрузки"})
    playlist_uid = created.json()["id"]
    uploads = await upload(api, tmp_path, 2)

    # a staging file lost after the upload: this track fails, the other one still imports and goes to the playlist
    async with session_scope() as s:
        lost = await s.get(Upload, uploads[0]["id"])
        assert lost is not None
        (get_settings().staging_dir / lost.staging_path).unlink()

    r = await api.post(
        "/api/uploads/commit", data={"payload": json.dumps(commit_payload(uploads, playlist_id=playlist_uid))}
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    assert await playlist_titles(api, playlist_uid) == ["Трек 1"]
