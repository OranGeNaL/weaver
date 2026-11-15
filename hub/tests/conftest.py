"""Shared fixtures: a migrated Postgres template and the Hub's test environment (fakes and helpers: support.py).

`TEST_DATABASE_URL` is required (see `app.db.test_database_url`). Migrations run once per session into a template
database; each xdist worker clones it, and every test starts from a truncated schema — no SQLite template file.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg import sql
from sqlalchemy.engine import make_url

from tests.support import ND


def _with_database(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def _libpq(url: str) -> str:
    """libpq form (psycopg, pg_dump): no SQLAlchemy driver suffix."""
    return "postgresql://" + url.split("://", 1)[1]


def _async_url(url: str) -> str:
    """The driver the Hub's async engine uses."""
    return "postgresql+asyncpg://" + url.split("://", 1)[1]


def _maintenance_url(url: str) -> str:
    return _libpq(_with_database(url, "postgres"))


def pg_url_of_current_run() -> str:
    from app.db import test_database_url

    return test_database_url()


def _drop_database(url: str, name: str) -> None:
    with psycopg.connect(_maintenance_url(url), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,))
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def pg_url() -> str:
    """The test server: required, Postgres-only, never the working database."""
    return pg_url_of_current_run()


# pg_advisory_lock key of the template creation: xdist workers do it exactly once between them
_TEMPLATE_LOCK = 0x74656D70  # "temp"


@pytest.fixture(scope="session")
def template_name(pg_url: str) -> Iterator[str]:
    """A database at alembic head; workers clone it instead of migrating again."""
    from app import db

    name = f"{make_url(pg_url).database}__template"
    with psycopg.connect(_maintenance_url(pg_url), autocommit=True) as conn, conn.cursor() as cur:
        # the lock is held for creation *and* migrations: another worker must not clone a half-built template
        cur.execute("SELECT pg_advisory_lock(%s)", (_TEMPLATE_LOCK,))
        try:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            if cur.fetchone() is None:
                _drop_database(pg_url, name)
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                db.run_migrations(_with_database(pg_url, name))
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_TEMPLATE_LOCK,))
    try:
        yield name
    finally:
        _drop_database(pg_url, name)


@pytest.fixture(scope="session")
def worker_url(pg_url: str, template_name: str, worker_id: str) -> Iterator[str]:
    """This xdist worker's own clone of the template; tests in it never see each other's data."""
    name = f"{make_url(pg_url).database}__{worker_id.replace('-', '_')}"
    _drop_database(pg_url, name)
    with psycopg.connect(_maintenance_url(pg_url), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(template_name))
        )
    try:
        yield _async_url(_with_database(pg_url, name))
    finally:
        _drop_database(pg_url, name)


def _truncate(url: str) -> None:
    with psycopg.connect(_libpq(url), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'")
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            cur.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(sql.Identifier(table)))


@pytest.fixture(autouse=True)
def clean_db(worker_url: str) -> None:
    """Every test starts from a clean schema (the previous test's rows are gone)."""
    _truncate(worker_url)


@pytest.fixture
def db_url(worker_url: str) -> str:
    """The test database URL: pass it to `app.db.init_engine()`."""
    return worker_url


@pytest.fixture
def hub_env(db_url: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    """Settings for create_app(): temp folders, a fake Navidrome, no background workers, no waiting.

    Yields the music root. Module caches that outlive a test are cleared before and after it.
    """
    music = tmp_path / "music"
    # the app's lifespan migrates/uses DATABASE_URL: in tests that is this worker's clone, never a working DB
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("MUSIC_ROOT", str(music))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONFIG_FILE", str(tmp_path / "none.yaml"))
    # the local backend is dev/test only; S3 smoke tests are opt-in (`HUB_S3_TESTS=1`, marker `s3`)
    monkeypatch.setenv("STORAGE_BACKEND", "fs")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MEDIA_BLOB_TOKEN", "test-blob-token")
    monkeypatch.setenv("NAVIDROME_URL", ND)
    monkeypatch.setenv("NAVIDROME_ADMIN_USER", "admin")
    monkeypatch.setenv("NAVIDROME_ADMIN_PASSWORD", "adminpw")
    monkeypatch.setenv("HUB_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("JWT_SIGNING_KEY", "test-jwt-signing-key")
    # the first administrator comes from the environment (no `admin/admin` default anywhere)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "adminpw")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "")
    monkeypatch.setenv("DISABLE_BACKGROUND", "true")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("ND_SCAN_POLL_S", "0")
    monkeypatch.setenv("EMBEDDER_URL", "")  # a test that needs it mocks its own
    monkeypatch.setenv("LASTFM_API_KEY", "")
    monkeypatch.setenv("SMTP_HOST", "")  # no letters; a test that sends them captures its own
    monkeypatch.setenv("MIX_EMBED_WAIT_S", "1")
    monkeypatch.setenv("MIX_POLL_S", "0")
    _clear_caches()
    yield music
    _clear_caches()


def _clear_caches() -> None:
    from app import accounts, config, google_auth, oidc, security, storage
    from app.library.navidrome import NavidromeAdmin
    from app.mixes import sources, wave
    from app.providers import ytdlp

    config.get_settings.cache_clear()
    storage.reset()
    security._fernet.cache_clear()
    accounts._failures.clear()
    google_auth._keys.clear()
    oidc._docs.clear()
    oidc._keys.clear()
    NavidromeAdmin.forget_token()
    ytdlp._media_hosts.clear()
    sources._lastfm_limiter = None
    wave._rt.clear()


def pytest_collection_modifyitems(config, items):
    """`HUB_S3_TESTS=1` enables the `s3` marker; the default `pytest` run skips those tests."""
    import os

    if os.environ.get("HUB_S3_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="S3 smoke tests: set HUB_S3_TESTS=1 (and S3_ENDPOINT/S3_BUCKET)")
    for item in items:
        if "s3" in item.keywords:
            item.add_marker(skip)
