"""Hot cache of blobs on the local disk: streaming, ffprobe/ffmpeg and the embedder read from here.

One file per `sha256`, filled from the object store on a miss, promoted from `.part` only when the size matches
`head`, evicted least recently used over `STORAGE_CACHE_MAX_BYTES`. Single-flight per hash: parallel readers of
the same track fill it once.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from app.storage.base import ObjectNotFound, StorageBackend

log = logging.getLogger(__name__)

CHUNK = 256 * 1024
PART_TTL_S = 3600


class HotCache:
    def __init__(self, root: Path, max_bytes: int):
        self.root = Path(root)
        self.max_bytes = max(0, max_bytes)
        self._inflight: dict[str, asyncio.Task[Path]] = {}
        self._lock = asyncio.Lock()

    def path_of(self, sha: str) -> Path:
        return self.root / sha

    def cached(self, sha: str) -> Path | None:
        p = self.path_of(sha)
        return p if p.is_file() else None

    async def touch(self, sha: str) -> None:
        p = self.path_of(sha)
        if p.is_file():
            await asyncio.to_thread(os.utime, p)

    async def ensure(self, backend: StorageBackend, sha: str, *, expected_size: int | None = None) -> Path:
        """A complete local copy of `audio/<sha>`; downloads it once for concurrent callers."""
        hit = self.cached(sha)
        if hit is not None:
            await self.touch(sha)
            return hit
        async with self._lock:
            task = self._inflight.get(sha)
            if task is None:
                task = asyncio.create_task(self._download(backend, sha, expected_size))
                self._inflight[sha] = task

                def done(t: asyncio.Task[Path], _sha: str = sha) -> None:
                    self._inflight.pop(_sha, None)
                    if not t.cancelled():
                        t.exception()  # nobody may be awaiting it any more

                task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _download(self, backend: StorageBackend, sha: str, expected_size: int | None) -> Path:
        key = f"audio/{sha}"
        info = await backend.head(key)
        if info is None:
            raise ObjectNotFound(key)
        size = info.size if expected_size is None else expected_size
        if info.size != size:
            raise ObjectNotFound(f"{key}: size {info.size} != expected {size}")
        self.root.mkdir(parents=True, exist_ok=True)
        dest = self.path_of(sha)
        part = dest.with_name(dest.name + ".part")
        written = 0
        try:
            with part.open("wb") as out:
                async for chunk in backend.iter_range(key, 0, None):
                    if not chunk:
                        continue
                    await asyncio.to_thread(out.write, chunk)
                    written += len(chunk)
            if written != size:
                raise OSError(f"{key}: cached {written} bytes, head says {size}")
            await asyncio.to_thread(os.replace, part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        await asyncio.to_thread(os.utime, dest)
        await asyncio.to_thread(self._prune)
        log.info("cache %s: %d bytes", sha[:12], written)
        return dest

    def _prune(self) -> int:
        if not self.root.is_dir():
            return 0
        files: list[tuple[float, int, Path]] = []
        total = 0
        now = time.time()
        for f in self.root.iterdir():
            if not f.is_file():
                continue
            st = f.stat()
            if f.name.endswith(".part"):
                if now - st.st_mtime > PART_TTL_S and f.name[: -len(".part")] not in self._inflight:
                    f.unlink(missing_ok=True)
                continue
            total += st.st_size
            files.append((st.st_mtime, st.st_size, f))
        removed = 0
        for _, size, f in sorted(files, key=lambda x: x[0]):
            if total <= self.max_bytes:
                break
            f.unlink(missing_ok=True)
            total -= size
            removed += 1
        return removed

    async def evict(self) -> int:
        """Housekeeping hook: drop least recently used files over the limit."""
        return await asyncio.to_thread(self._prune)

    def total_bytes(self) -> int:
        if not self.root.is_dir():
            return 0
        return sum(f.stat().st_size for f in self.root.iterdir() if f.is_file() and not f.name.endswith(".part"))

    def reset(self) -> None:
        for t in list(self._inflight.values()):
            t.cancel()
        self._inflight.clear()
