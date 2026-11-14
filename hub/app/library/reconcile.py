"""Catalog reconcile: detect a track without its blob, an album without its cover, a blob without references.

The reconcile is detective only: it sets or clears `tracks.integrity_issue` / `albums.integrity_issue`, logs orphan
blobs and never indexes an object that appeared in the bucket outside the Hub. Deletion stays with the `storage` GC
(`app.storage.gc`), so two owners never free the same bytes.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Album, Blob, Track
from app.storage import backend, blobs

log = logging.getLogger(__name__)

MISSING_BLOB = "missing_blob"
MISSING_COVER = "missing_cover"

# the last pass, reported by the admin health endpoint (an in-process diagnostic, not a source of truth)
last_result: dict[str, Any] = {}


async def _missing_objects(s: AsyncSession, rows: list[Blob], b: Any) -> set[int]:
    """Blob ids whose committed object is absent from the bucket (one HEAD per distinct sha)."""
    missing: set[int] = set()
    checked: dict[str, bool] = {}
    for blob in rows:
        if blob.sha256 not in checked:
            try:
                checked[blob.sha256] = (await b.head(blobs.object_key(blob.sha256, blob.kind))) is not None
            except Exception as e:
                # an unavailable bucket is not evidence that the object is gone: keep the current marker
                log.warning("catalog_reconcile: cannot head %s: %r", blob.sha256[:12], e)
                checked[blob.sha256] = True
        if not checked[blob.sha256]:
            missing.add(blob.id)
    return missing


async def catalog_reconcile(s: AsyncSession) -> dict[str, int]:
    """One detective pass; returns counters and remembers the result for health."""
    started = time.time()
    b = backend()

    track_rows = (await s.execute(select(Track, Blob).outerjoin(Blob, Blob.id == Track.blob_id))).all()
    blobs_in_tracks = [blob for _t, blob in track_rows if blob is not None]
    missing_objects = await _missing_objects(s, blobs_in_tracks, b)

    missing_blob = cleared_blob = 0
    for track, blob in track_rows:
        broken = blob is None or blob.status == blobs.DELETED or blob.id in missing_objects
        if broken:
            if track.integrity_issue != MISSING_BLOB:
                track.integrity_issue = MISSING_BLOB
                missing_blob += 1
        elif track.integrity_issue == MISSING_BLOB:
            track.integrity_issue = None
            cleared_blob += 1

    album_rows = (await s.execute(select(Album, Blob).outerjoin(Blob, Blob.id == Album.cover_blob_id))).all()
    cover_blobs = [blob for _a, blob in album_rows if blob is not None]
    missing_covers = await _missing_objects(s, cover_blobs, b)
    broken_cover = missing_cover = cleared_cover = 0
    for album, blob in album_rows:
        broken = blob is None or blob.status == blobs.DELETED or blob.id in missing_covers
        if broken:
            broken_cover += 1
            if album.integrity_issue != MISSING_COVER:
                album.integrity_issue = MISSING_COVER
                missing_cover += 1
        elif album.integrity_issue == MISSING_COVER:
            album.integrity_issue = None
            cleared_cover += 1

    # a committed blob no track or album points at: reported, never deleted here
    referenced = {t.blob_id for t, _ in track_rows if t.blob_id is not None}
    referenced |= {a.cover_blob_id for a, _ in album_rows if a.cover_blob_id is not None}
    orphan = [
        blob.sha256
        for blob in (await s.execute(select(Blob).where(Blob.status == blobs.COMMITTED))).scalars()
        if blob.id not in referenced
    ]
    for sha in orphan:
        log.warning("catalog_reconcile: orphan_blob %s (freed later by the storage GC)", sha[:12])

    await s.flush()
    result = {
        "missing_blob": missing_blob,
        "cleared_missing_blob": cleared_blob,
        "missing_cover": missing_cover,
        "cleared_missing_cover": cleared_cover,
        "broken_covers": broken_cover,
        "orphan_blob": len(orphan),
        "elapsed_ms": round((time.time() - started) * 1000),
    }
    last_result.clear()
    last_result.update({**result, "checked_at": time.time()})
    log.info("catalog_reconcile: %s", result)
    return result


__all__ = ["catalog_reconcile", "last_result"]
