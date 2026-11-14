"""Garbage collection of blobs: pending leftovers, deleted rows and objects no row references.

The daily housekeeping runs it after the trash purge: track rows older than `trash_days` are gone by then, so a
blob without references can lose its object. A bucket listing catches objects whose upload never reached the DB.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Album, Blob, Track
from app.storage import backend, blobs

log = logging.getLogger(__name__)

PENDING_TTL_S = 86400  # an object under `pending/` older than a day is a leftover of a crash
ORPHAN_TTL_S = 86400  # an `audio/` object with no row at all is left alone this long


async def _referenced_blob_ids(s: AsyncSession) -> set[int]:
    track_ids = {i for i in (await s.execute(select(Track.blob_id))).scalars() if i is not None}
    cover_ids = {i for i in (await s.execute(select(Album.cover_blob_id))).scalars() if i is not None}
    return track_ids | cover_ids


async def gc(s: AsyncSession, *, now: float | None = None) -> dict[str, int]:
    """Delete unreferenced blobs (DB + object) and stray objects in the bucket; returns counters."""
    now = now if now is not None else time.time()
    b = backend()
    referenced = await _referenced_blob_ids(s)
    deleted_objects = 0
    deleted_rows = 0
    deleted_keys = 0

    rows = (await s.execute(select(Blob))).scalars().all()
    for blob in rows:
        if blob.id in referenced or blob.status == "deleted":
            continue
        age = now - _ts(blob.created_at)
        if blob.status == "pending" and age < PENDING_TTL_S:
            continue
        # an unreferenced committed blob keeps its object for a grace period: a rollback of the importing
        # transaction may still leave a row that points at it
        ttl = PENDING_TTL_S if blob.status == "pending" else ORPHAN_TTL_S
        if now - _ts(blob.committed_at or blob.created_at) < ttl:
            continue
        removed = True
        try:
            await b.delete(blobs.object_key(blob.sha256, blob.kind))
            deleted_objects += 1
            for key in await b.list(f"manifests/{blob.sha256}/"):
                await b.delete(key)
        except Exception as e:  # a failed delete stays committed and is retried by the next GC
            removed = False
            log.warning("gc: cannot delete object %s: %r", blob.sha256[:12], e)
        if removed:
            blob.status = "deleted"
            deleted_rows += 1
    await s.flush()

    # objects in the bucket that no blob row knows about (a crash before the DB row; a botched promotion)
    known_sha = set((await s.execute(select(Blob.sha256).where(Blob.status != "deleted"))).scalars())
    aged = getattr(b, "list_aged", None)
    if aged is not None:
        for key, mtime in await aged("audio/"):
            sha = key.split("/", 1)[-1]
            if sha in known_sha or now - mtime < ORPHAN_TTL_S:
                continue
            await b.delete(key)
            deleted_keys += 1
        for key, mtime in await aged("pending/"):
            if now - mtime < PENDING_TTL_S:
                continue
            await b.delete(key)
            deleted_keys += 1
        for key, mtime in await aged("covers/"):
            sha = key.split("/", 1)[-1]
            if sha in known_sha or now - mtime < ORPHAN_TTL_S:
                continue
            await b.delete(key)
            deleted_keys += 1

    if deleted_objects or deleted_rows or deleted_keys:
        log.info("blob gc: objects %s, rows %s, stray bucket keys %s", deleted_objects, deleted_rows, deleted_keys)
    return {"objects": deleted_objects, "rows": deleted_rows, "keys": deleted_keys}


def _ts(value) -> float:
    return value.timestamp() if value is not None else 0.0


async def gc_tracks_and_blobs(trash_days: int, *, now: float | None = None) -> dict[str, int]:
    """Delete trashed track rows after `trash_days`, then GC the blobs they no longer reference."""
    from app.library.editor import purge_trash

    await purge_trash(trash_days)
    async with session_scope() as s:
        return await gc(s, now=now)


async def blobs_without_refs(s: AsyncSession) -> list[Blob]:
    """Exposed for diagnostics/tests: committed blobs no track or album points at."""
    referenced = await _referenced_blob_ids(s)
    rows = (await s.execute(select(Blob).where(Blob.status == "committed"))).scalars().all()
    return [b for b in rows if b.id not in referenced]


__all__ = ["blobs_without_refs", "gc", "gc_tracks_and_blobs"]
