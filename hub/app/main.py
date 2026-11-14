"""HomeMusic Hub: FastAPI app (API + PWA static files) with in-process job workers on a Linux host."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import get_settings
from app.logging_setup import setup_logging
from app.oidc import OidcError

log = logging.getLogger("app")


def _import_handlers() -> None:
    # registering job handlers is a side effect of importing these modules
    import app.imports.core
    import app.jobs.downloads
    import app.mixes.core

    try:
        import app.ai.pipeline  # noqa: F401
    except ImportError:
        log.warning("assistant pipeline not available")


def _validate_secrets(s) -> None:
    """The Hub never starts with an unsafe default: the JWT key and the secret key are required."""
    missing = [
        name
        for name, value in (("JWT_SIGNING_KEY", s.jwt_signing_key), ("HUB_SECRET_KEY", s.hub_secret_key))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Не заданы обязательные ключи: {', '.join(missing)}. "
            "Без них Hub не стартует: сгенерируйте их (см. README, «Ключи и секреты»)."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    _validate_secrets(s)
    s.validate_storage()  # `fs` must not run as production
    for d in (s.staging_dir, s.backups_dir, s.cache_dir):
        d.mkdir(parents=True, exist_ok=True)
    if s.storage_backend == "fs":
        s.objects_dir.mkdir(parents=True, exist_ok=True)
    db.init_engine()  # fails with a clear error without DATABASE_URL
    await db.connect_with_retry()
    await db.ensure_search_locale()
    await asyncio.to_thread(db.run_migrations)

    from app import bootstrap

    await bootstrap.bootstrap_from_env()
    from app import storage

    await storage.startup()

    from app import listen
    from app.housekeeping import scheduler_loop
    from app.jobs.queue import WorkerPool, recover_stale
    from app.mixes import smart, wave
    from app.mixes.core import schedule_loop
    from app.plays import scrobbles_loop
    from app.services import svc

    svc.start()
    _import_handlers()
    pool = WorkerPool(s.cfg.jobs.workers)
    tasks: list[asyncio.Task] = []
    if not s.disable_background:
        n = await recover_stale()
        if n:
            log.info("re-queued %s interrupted jobs", n)
        pool.start()
        tasks.append(asyncio.create_task(scheduler_loop(), name="scheduler"))
        tasks.append(asyncio.create_task(scrobbles_loop(), name="scrobbles"))
        tasks.append(asyncio.create_task(schedule_loop(), name="mixes"))
        # `playlist_adds_loop` returns at stage 5: ND playlist effects are deferred until local playlists exist
    log.info("hub started")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await pool.stop()
        await listen.reset()
        from app import storage as storage_layer

        storage_layer.reset()
        await wave.reset()
        await smart.reset()
        await svc.stop()
        await db.dispose()


def create_app() -> FastAPI:
    from app.api import (
        admin,
        auth,
        catalog,
        downloads,
        events,
        imports,
        insights,
        library,
        listen,
        media,
        medialib,
        mixes,
        playlists,
        plays,
        search,
        smart_shuffle,
        system,
        uploads,
        wave,
    )

    app = FastAPI(
        title="HomeMusic Hub",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    @app.exception_handler(OidcError)
    async def _oidc_error(_request: Request, exc: OidcError) -> JSONResponse:
        """An OIDC step failed: the provider's error becomes the API's Russian `detail` with its status."""
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    for r in (
        auth.router,
        search.router,
        catalog.router,
        downloads.router,
        listen.router,
        library.router,
        media.router,
        medialib.router,
        uploads.router,
        playlists.router,
        plays.router,
        mixes.router,
        wave.router,
        smart_shuffle.router,
        imports.router,
        events.router,
        system.router,
        admin.router,
        insights.router,
    ):
        app.include_router(r)
    try:
        from app.api import assistant

        app.include_router(assistant.router)
    except ImportError:
        pass

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)

    static = get_settings().static_dir
    if (static / "index.html").is_file():
        if (static / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Не найдено"}, status_code=404)
            f = (static / path).resolve()
            if path and f.is_file() and static.resolve() in f.parents:
                headers = {"Cache-Control": "no-cache"} if f.name in ("sw.js", "manifest.webmanifest") else None
                return FileResponse(f, headers=headers)
            return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
