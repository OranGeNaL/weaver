"""Service access to a blob by its sha256: `/api/media/blob/<sha256>`.

The embedder normally gets a presigned URL; when the backend cannot presign (local storage, a provider that
disallows it) it asks the Hub with `MEDIA_BLOB_TOKEN` in `Authorization: Bearer`. No user cookie is required.
"""

from __future__ import annotations

import asyncio
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Blob
from app.storage import backend, blobs, cache

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/media", tags=["media"])


def _authorized(request: Request) -> bool:
    expected = get_settings().media_blob_token
    header = request.headers.get("authorization", "")
    if not expected or not header.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(header[7:].strip(), expected)


def _parse_range(header: str | None, size: int) -> tuple[int, int | None, bool]:
    if not header or not header.startswith("bytes="):
        return 0, None, False
    first, _, last = header[len("bytes=") :].split(",", 1)[0].partition("-")
    try:
        if not first:
            n = int(last or 0)
            return max(0, size - n), size - 1, True
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return 0, None, False
    return start, min(end, size - 1), True


@router.get("/blob/{sha256}")
async def blob(
    sha256: str,
    request: Request,
    s: AsyncSession = Depends(get_session, scope="function"),
):
    if not _authorized(request):
        raise HTTPException(401, "Нужен сервисный токен", headers={"WWW-Authenticate": "Bearer"})
    row = (
        await s.execute(select(Blob).where(Blob.sha256 == sha256, Blob.status == blobs.COMMITTED).limit(1))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Блоб не найден")

    media_type = row.content_type or "application/octet-stream"
    headers = {"accept-ranges": "bytes", "etag": row.sha256, "cache-control": "private, max-age=600"}
    hit = cache().cached(row.sha256)
    if hit is None and await backend().head(blobs.object_key(row.sha256, row.kind)) is None:
        raise HTTPException(404, "Объект блоба отсутствует в хранилище")
    if hit is None and not request.headers.get("range"):
        hit = await cache().ensure(backend(), row.sha256, expected_size=row.size)
    if hit is not None:
        return FileResponse(hit, media_type=media_type, headers=headers)

    start, end, partial = _parse_range(request.headers.get("range"), row.size)
    if start >= row.size:
        return StreamingResponse(iter(()), status_code=416, headers={**headers, "content-range": f"bytes */{row.size}"})
    fill = asyncio.create_task(cache().ensure(backend(), row.sha256, expected_size=row.size))
    fill.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    h = {
        **headers,
        "content-length": str(row.size if end is None else end - start + 1),
    }
    if partial:
        h["content-range"] = f"bytes {start}-{end if end is not None else row.size - 1}/{row.size}"
    return StreamingResponse(
        backend().iter_range(blobs.object_key(row.sha256, row.kind), start, end),
        status_code=206 if partial else 200,
        headers=h,
        media_type=media_type,
    )
