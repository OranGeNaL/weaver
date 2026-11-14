"""Local-directory backend: tests and development (`STORAGE_BACKEND=fs`). Never production."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from app.storage.base import (
    ObjectInfo,
    ObjectNotFound,
    PresignUnavailable,
)

_CHUNK = 256 * 1024


class LocalBackend:
    """Objects as files under `root`, key -> path with forward slashes. Metadata in a sidecar `.meta.json`."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path_of(self, key: str) -> Path:
        """Resolve an object key inside `root`; keys with `..` or an absolute path are rejected."""
        key = key.strip("/")
        parts = key.split("/")
        if not key or any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"bad object key: {key!r}")
        root = self.root.resolve()
        path = (root / Path(*parts)).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"object key escapes the storage root: {key!r}")
        return path

    def _meta_path(self, key: str) -> Path:
        p = self.path_of(key)
        return p.with_name(p.name + ".meta.json")

    async def put(
        self,
        key: str,
        data: bytes | Path,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectInfo:
        def write() -> ObjectInfo:
            dest = self.path_of(key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            if isinstance(data, Path):
                shutil.copyfile(data, tmp)
            else:
                tmp.write_bytes(data)
            tmp.replace(dest)
            size = dest.stat().st_size
            meta = dict(metadata or {})
            sidecar = {"content_type": content_type, "metadata": meta}
            self._meta_path(key).write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
            return ObjectInfo(key=key, size=size, content_type=content_type, metadata=meta)

        return await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            p = self.path_of(key)
            if not p.is_file():
                raise ObjectNotFound(key)
            return p.read_bytes()

        return await asyncio.to_thread(read)

    async def get_range(self, key: str, start: int, end: int | None = None) -> bytes:
        def read() -> bytes:
            p = self.path_of(key)
            if not p.is_file():
                raise ObjectNotFound(key)
            with p.open("rb") as f:
                f.seek(start)
                return f.read() if end is None else f.read(max(0, end - start + 1))

        return await asyncio.to_thread(read)

    async def iter_range(self, key: str, start: int = 0, end: int | None = None) -> AsyncIterator[bytes]:
        p = self.path_of(key)
        if not p.is_file():
            raise ObjectNotFound(key)
        remaining = None if end is None else end - start + 1
        with p.open("rb") as f:
            f.seek(start)
            while remaining is None or remaining > 0:
                n = _CHUNK if remaining is None else min(_CHUNK, remaining)
                chunk = await asyncio.to_thread(f.read, n)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk

    async def head(self, key: str) -> ObjectInfo | None:
        def info() -> ObjectInfo | None:
            p = self.path_of(key)
            if not p.is_file():
                return None
            meta: dict[str, str] = {}
            ctype: str | None = None
            side = self._meta_path(key)
            if side.is_file():
                try:
                    data = json.loads(side.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
                meta = dict(data.get("metadata") or {})
                ctype = data.get("content_type")
            return ObjectInfo(key=key, size=p.stat().st_size, content_type=ctype, metadata=meta)

        return await asyncio.to_thread(info)

    async def delete(self, key: str) -> None:
        def remove() -> None:
            self.path_of(key).unlink(missing_ok=True)
            self._meta_path(key).unlink(missing_ok=True)

        await asyncio.to_thread(remove)

    async def presign_get(self, key: str, expires_s: int = 3600) -> str:
        raise PresignUnavailable("local storage has no presigned URLs")

    async def list(self, prefix: str = "") -> list[str]:
        def walk() -> list[str]:
            if not self.root.is_dir():
                return []
            out: list[str] = []
            for p in self.root.rglob("*"):
                if not p.is_file() or p.name.endswith(".meta.json") or p.name.endswith(".part"):
                    continue
                key = p.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    out.append(key)
            return sorted(out)

        return await asyncio.to_thread(walk)

    async def copy(self, src: str, dst: str) -> None:
        def do() -> None:
            source = self.path_of(src)
            if not source.is_file():
                raise ObjectNotFound(src)
            dest = self.path_of(dst)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            shutil.copyfile(source, tmp)
            tmp.replace(dest)
            side = self._meta_path(src)
            if side.is_file():
                self._meta_path(dst).write_text(side.read_text(encoding="utf-8"), encoding="utf-8")

        await asyncio.to_thread(do)

    async def check(self) -> bool:
        return await asyncio.to_thread(lambda: self.root.is_dir() or self.root.parent.is_dir())

    async def list_aged(self, prefix: str = "") -> list[tuple[str, float]]:
        """Object keys with their modification time (seconds since epoch), for GC's TTLs."""

        def walk() -> list[tuple[str, float]]:
            if not self.root.is_dir():
                return []
            out: list[tuple[str, float]] = []
            for p in self.root.rglob("*"):
                if not p.is_file() or p.name.endswith(".meta.json") or p.name.endswith(".part"):
                    continue
                key = p.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    out.append((key, p.stat().st_mtime))
            return sorted(out)

        return await asyncio.to_thread(walk)
