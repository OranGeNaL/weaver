"""Postgres engine, async sessions and Alembic migrations on startup."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import func, literal, literal_column, select, table
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

# DATABASE_URL may be written either way; the Hub's app layer needs asyncpg, Alembic's CLI — a sync driver
_ASYNC_PREFIX = "postgresql+asyncpg://"
_SYNC_PREFIX = "postgresql+psycopg://"
_BARE_PREFIX = "postgresql://"

# connect retries: a restart next to a still-starting Postgres must not crash the Hub at once
CONNECT_ATTEMPTS = 10
CONNECT_BASE_DELAY_S = 0.5


def database_url() -> str:
    """The Hub's Postgres URL from DATABASE_URL. Postgres only: there is no SQLite fallback."""
    url = (get_settings().database_url or os.environ.get("DATABASE_URL", "")).strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL не задан. Hub работает только с Postgres: укажите "
            "DATABASE_URL=postgresql+asyncpg://пользователь:пароль@хост:5432/база в .env"
        )
    if url.startswith(_SYNC_PREFIX) or url.startswith(_BARE_PREFIX):
        return _ASYNC_PREFIX + url.split("://", 1)[1]
    if not url.startswith(_ASYNC_PREFIX):
        raise RuntimeError(
            "DATABASE_URL должен указывать на Postgres (postgresql+asyncpg://…): SQLite и файлы БД не поддерживаются"
        )
    return url


def sync_database_url(url: str | None = None) -> str:
    """The same database for Alembic, which runs on the sync psycopg driver."""
    source = url or database_url()
    if source.startswith(_SYNC_PREFIX):
        return source
    for prefix in (_ASYNC_PREFIX, _BARE_PREFIX):
        if source.startswith(prefix):
            return _SYNC_PREFIX + source.split("://", 1)[1]
    raise RuntimeError("DATABASE_URL должен указывать на Postgres: SQLite не поддерживается")


def test_database_url() -> str:
    """TEST_DATABASE_URL of the test run: required, Postgres only, never the working database."""
    url = (os.environ.get("TEST_DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "TEST_DATABASE_URL не задана. Тесты Hub требуют отдельный Postgres: "
            "TEST_DATABASE_URL=postgresql+asyncpg://пользователь:пароль@хост:5432/база_тестов"
        )
    working = (get_settings().database_url or os.environ.get("DATABASE_URL", "")).strip()
    if working and url == working:
        raise RuntimeError("TEST_DATABASE_URL совпадает с DATABASE_URL: тесты не должны трогать рабочую базу")
    return sync_for_tests(url)


def sync_for_tests(url: str) -> str:
    """Normalize a test URL to the sync driver (tests create and migrate the database themselves)."""
    for prefix in (_ASYNC_PREFIX, _BARE_PREFIX):
        if url.startswith(prefix):
            return _SYNC_PREFIX + url.split("://", 1)[1]
    return url


def init_engine(url: str | None = None) -> AsyncEngine:
    """Create the async engine; `url` defaults to DATABASE_URL (tests pass TEST_DATABASE_URL)."""
    global _engine, _sessionmaker
    _engine = create_async_engine(url or database_url(), pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def connect_with_retry(attempts: int = CONNECT_ATTEMPTS, base_delay_s: float = CONNECT_BASE_DELAY_S) -> None:
    """Wait for a reachable database, then fail with a clear Russian error."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with engine().connect() as conn:
                await conn.execute(select(literal(1)))
            return
        except Exception as e:  # connection refused, bad credentials, still starting
            last = e
            if attempt == attempts:
                break
            delay = base_delay_s * min(attempt, 5)
            log.warning("postgres недоступен (попытка %s/%s): %r; повтор через %.1f с", attempt, attempts, e, delay)
            await asyncio.sleep(delay)
    raise RuntimeError(f"Не удалось подключиться к Postgres по DATABASE_URL: {last!r}") from last


async def ensure_search_locale() -> None:
    """UTF8 and a non-C ctype: otherwise `lower()`/`ILIKE` mangle Cyrillic on an external Postgres."""
    async with engine().connect() as conn:
        row = (
            await conn.execute(
                select(
                    func.pg_encoding_to_char(literal_column("encoding")),
                    literal_column("datctype"),
                )
                .select_from(table("pg_database"))
                .where(literal_column("datname") == func.current_database())
            )
        ).first()
    if row is None:
        return
    encoding, datctype = row
    if str(encoding).upper() != "UTF8" or str(datctype).upper() in ("C", "POSIX"):
        raise RuntimeError(
            "База Postgres должна быть в UTF8 с non-C ctype (например C.UTF-8): иначе поиск по кириллице "
            f"регистронезависимым не будет (server_encoding={encoding}, datctype={datctype})"
        )


def engine() -> AsyncEngine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session that commits on success and rolls back on error."""
    async with sessionmaker()() as s:
        try:
            yield s
            await s.commit()
        except BaseException:
            await s.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Use with `scope="function"`: the commit then happens before the response is sent
    (with the default request scope a client could see a stale state right after a write, e.g. 401 after login),
    and long responses (SSE) don't hold a connection."""
    async with session_scope() as s:
        yield s


def run_migrations(url: str | None = None) -> None:
    """alembic upgrade head (sync, called once at startup before the event loop work starts)."""
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sync_database_url(url))
    # keep the app's JSON logging (env.py would otherwise apply alembic.ini's logging config)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
