"""Periodic maintenance: DB backup, trash/staging cleanup, cache purge, provider smoke test (NFR-5, NFR-6)."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.db import sync_database_url

log = logging.getLogger(__name__)

KEEP_BACKUPS = 7
SCRATCH_DB = "homemusic_restore_check"


def libpq_url(url: str | None = None) -> str:
    """The Postgres URL as libpq (pg_dump/pg_restore) expects it: no SQLAlchemy driver suffix."""
    value = sync_database_url(url)
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    # never put the URL on the command line: it holds the password, and logs must not see it
    return subprocess.run(args, check=True, capture_output=True, text=True, env=os.environ.copy())


def backup_hub_db() -> Path | None:
    """pg_dump --format=custom into backups/hub; rotation happens only after a successful dump."""
    s = get_settings()
    dest_dir = s.backups_dir / "hub"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / f"hub-{stamp}.dump"
    tmp = dest_dir / f".hub-{stamp}.part"
    url = libpq_url()
    env = os.environ.copy()
    env["PGPASSWORD"] = os.environ.get("PGPASSWORD", "")
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(tmp), "--dbname", url],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        # keep the previous copies: a broken dump must not shrink the history
        log.exception("hub db backup failed")
        raise
    for old in sorted(dest_dir.glob("hub-*.dump"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


def _psql_admin_url() -> str:
    """The same server, but the maintenance database: CREATE/DROP DATABASE cannot run inside the target."""
    url = libpq_url()
    head, _, _tail = url.rpartition("/")
    return f"{head}/postgres"


def _counts(connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in ("users", "albums", "tracks", "play_events"):
        with connection.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table}")
            out[table] = int(cur.fetchone()[0])
    return out


def verify_backup(dump: Path) -> bool:
    """pg_restore the dump into a scratch database and compare the schema and row counts."""
    import psycopg

    scratch = SCRATCH_DB
    source_url = libpq_url()
    with psycopg.connect(_psql_admin_url(), autocommit=True) as admin:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (scratch,))
            cur.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
            cur.execute(f'CREATE DATABASE "{scratch}"')
    try:
        _run(
            [
                "pg_restore",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                libpq_url().replace(f"/{libpq_url().rsplit('/', 1)[-1]}", f"/{scratch}"),
                str(dump),
            ]
        )
        with (
            psycopg.connect(source_url) as source,
            psycopg.connect(libpq_url().replace(f"/{libpq_url().rsplit('/', 1)[-1]}", f"/{scratch}")) as restored,
        ):
            before, after = _counts(source), _counts(restored)
        same = before == after
        (log.info if same else log.warning)("restore check: %s (%s)", "ok" if same else "MISMATCH", after)
        return same
    finally:
        with psycopg.connect(_psql_admin_url(), autocommit=True) as admin, admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (scratch,))
            cur.execute(f'DROP DATABASE IF EXISTS "{scratch}"')


async def smoke_test_providers() -> dict[str, str]:
    """Search a well-known track on every provider; a parser that returns nothing means the layout changed."""
    from app.providers.base import TrackQuery
    from app.services import svc

    q = TrackQuery(artist="Кино", title="Группа крови")
    out: dict[str, str] = {}
    for name, p in svc.providers.items():
        try:
            res = await p.search(q, limit=5)
            out[name] = f"ok ({len(res)})" if res else "EMPTY — возможно, сменилась вёрстка"
        except Exception as e:
            out[name] = f"error: {e!r}"[:200]
    bad = {k: v for k, v in out.items() if not v.startswith("ok")}
    (log.warning if bad else log.info)("provider smoke test: %s", out)
    return out


async def run_daily() -> None:
    from app.accounts import purge_email_codes
    from app.api.uploads import purge_stale_uploads
    from app.catalog.cache import cache_purge_expired
    from app.db import session_scope
    from app.library.reconcile import catalog_reconcile
    from app.storage import cache as storage_cache
    from app.storage.gc import gc_tracks_and_blobs
    from app.tokens import purge_expired as purge_expired_refresh_tokens

    cfg = get_settings().cfg
    try:
        path = await asyncio.to_thread(backup_hub_db)
        log.info("hub db backup: %s", path)
        if path is not None:
            await asyncio.to_thread(verify_backup, path)
    except Exception:
        log.exception("hub db backup failed")
    try:
        gc_stats = await gc_tracks_and_blobs(cfg.library.trash_days)
        m = await asyncio.to_thread(purge_stale_uploads)
        c = await cache_purge_expired()
        e = await purge_email_codes()
        evicted = await storage_cache().evict()
        async with session_scope() as s:
            reconcile = await catalog_reconcile(s)
            refresh_pruned = await purge_expired_refresh_tokens(s)
        log.info(
            "housekeeping: trash+gc %s, stale uploads %s, cache rows %s, email codes %s, hot-cache evicted %s, "
            "catalog reconcile %s, refresh tokens %s",
            gc_stats,
            m,
            c,
            e,
            evicted,
            reconcile,
            refresh_pruned,
        )
    except Exception:
        log.exception("housekeeping failed")
    # the provider smoke test is isolated: a cleanup failure must not skip it
    await smoke_test_providers()


async def scheduler_loop() -> None:
    """Runs `run_daily` at cfg.library.backup_hour local time every day."""
    hour = get_settings().cfg.library.backup_hour
    while True:
        now = datetime.now()
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        await run_daily()
