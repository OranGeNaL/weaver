"""Catalog commit: one transaction from a committed blob to a `done` download item.

The download pipeline used to finish an item only after Navidrome reported the scanned song (`nd_scan`). The Hub's
own catalog is now the source of truth: once the blob and the track row are in one transaction, the item is `done`
and carries the public `uid` (and the deprecated `library_track_id`). Navidrome linking is a separate, best-effort
`nd_sync` until stage 5/7; it never decides whether the catalog record exists.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Blob, DownloadItem, Track


async def catalog_commit(s: AsyncSession, item: DownloadItem, track: Track) -> dict[str, object]:
    """Bind the item to the track/blob and make it `done` (idempotent for the same `(item, blob)` pair)."""
    if track.blob_id is None:
        raise ValueError(f"трек {track.uid} без блоба: фиксация каталога невозможна")
    blob = await s.get(Blob, track.blob_id)
    if blob is None or blob.sha256 is None:
        raise ValueError(f"блоб трека {track.uid} не найден")
    already = item.track_id == track.id and item.blob_sha256 == blob.sha256 and item.status == "done"
    item.track_id = track.id
    item.blob_sha256 = blob.sha256
    item.status = "done"
    item.progress = 1
    item.error = None
    # the public identifier and the deprecated int alias live next to the item until stage 7
    item.query_json = {
        **(item.query_json or {}),
        "library_track_id": track.id,
        "uid": track.uid,
    }
    await s.flush()
    return {"idempotent": already, "track_id": track.id, "uid": track.uid, "sha256": blob.sha256}


__all__ = ["catalog_commit"]
