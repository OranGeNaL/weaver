"""Download pipeline (PLAN 8.3, 8.4): catalog meta → dedupe → providers → fetch → postcheck → tag → import.

Job kinds:
- `expand_batch`  — album/artist/track refs → download items
- `download_item` — one track end to end, ends in `done` after `catalog_commit` (or at once if already owned)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import DownloadItemOut
from app.catalog.deezer import CatalogNotFound
from app.catalog.models import CatalogAlbum, CatalogTrack, split_ref
from app.catalog.yandex import russian_original
from app.config import get_settings
from app.db import session_scope
from app.events import bus
from app.jobs.queue import PermanentError, enqueue, handler, notify, on_final_failure
from app.library import probe
from app.library.catalog import catalog_commit
from app.library.importer import ImportSpec, cleanup_dir, import_file, staging_dir_for
from app.library.owned import find_existing, quiet, star_only
from app.matching.select import (
    classify,
    match_catalog,
    needs_uncensored,
    rank_candidates,
    trusted_original,
    uploaded_in_time,
)
from app.models import DownloadBatch, DownloadItem, Job, PendingPlaylistAdd, Track
from app.net import make_client
from app.providers.base import Candidate, ProviderError, TrackQuery, file_ref
from app.services import svc

# an arbitrary constant key: every process_playlist_adds run in one server serializes on it
PLAYLIST_ADDS_LOCK_KEY = 0x706C6179  # "play"

log = logging.getLogger(__name__)

TERMINAL = ("done", "failed", "cancelled", "needs_review")
FAILED = ("failed", "needs_review")
USER_FLAGS = (
    "star",
    "requested",
    "star_event_id",
    "star_cancelled",
    "star_failed",
    "favorite_client",
    "favorite_context",
)
MAX_TRIES_PER_PROVIDER = 3
MAX_SHOWN = 15  # candidates kept on the item for the user to pick from
MAX_SHOWN_REUPLOADS = 5
MAYBE_CENSORED = "Возможна цензура: копий без цензуры не нашлось"


# --- item state + events ------------------------------------------------------------------


async def item_playlists(s: AsyncSession, items: list[DownloadItem]) -> dict[int, str | None]:
    """item id -> the batch's target playlist id for `item_out`."""
    batch_ids = {i.batch_id for i in items}
    playlists = (
        dict(
            (
                await s.execute(
                    select(DownloadBatch.id, DownloadBatch.target_playlist_id).where(DownloadBatch.id.in_(batch_ids))
                )
            ).all()
        )
        if batch_ids
        else {}
    )
    return {i.id: playlists.get(i.batch_id) if i.batch_id is not None else None for i in items}


def item_out(item: DownloadItem, *, detail: bool = False, playlist_id: str | None = None) -> DownloadItemOut:
    q = (item.query_json or {}).get("query", {})
    meta = (item.query_json or {}).get("meta") or {}
    return DownloadItemOut(
        id=item.id,
        batch_id=item.batch_id,
        status=item.status,
        progress=item.progress or 0,
        artist=q.get("artist", ""),
        title=q.get("title", ""),
        album=meta.get("album") or q.get("album"),
        provider=item.provider,
        error=item.error,
        warnings=item.warnings or [],
        track_id=item.track_id,
        library_track_id=(item.query_json or {}).get("library_track_id"),
        uid=(item.query_json or {}).get("uid"),
        playlist_id=playlist_id,
        track_ref=item_ref(item),
        star=quiet(item.query_json),
        liked=bool((item.query_json or {}).get("star")) and not bool((item.query_json or {}).get("star_failed")),
        import_id=((item.query_json or {}).get("import") or {}).get("id"),
        candidates=[Candidate(**c) for c in item.candidates_json or []] if detail else None,
        chosen=Candidate(**item.chosen_candidate_json) if item.chosen_candidate_json else None,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def item_ref(item: DownloadItem) -> str | None:
    """The catalog track the item was made from, or the provider's file it downloads (a `file_ref`)."""
    if ref := ((item.query_json or {}).get("meta") or {}).get("track_ref"):
        return ref
    c = item.chosen_candidate_json
    return file_ref(c["provider"], c["provider_id"]) if c else None


async def update_item(item_id: int, **fields: Any) -> DownloadItem | None:
    async with session_scope() as s:
        item = await s.get(DownloadItem, item_id)
        if item is None:
            return None
        if "query_json" in fields:
            # ♥ / an explicit download may have joined this item meanwhile (`api.downloads.join_active`): the job's
            # copy of query_json predates it
            cur = item.query_json or {}
            fields["query_json"] = {**fields["query_json"], **{k: cur[k] for k in USER_FLAGS if k in cur}}
        for k, v in fields.items():
            setattr(item, k, v)
        if item.status in FAILED and star_only(item.query_json):
            # a download queued only for ♥ that failed is shown to the user as a download from now on (Downloads,
            # notifications): the ♥ doesn't just vanish. It still stars the song once a file is picked.
            item.query_json = {**item.query_json, "requested": True}
        if item.status in (*FAILED, "cancelled"):
            from app.favorites import download_failed

            await download_failed(s, item)
        await s.flush()
        await s.refresh(item)
        if "status" in fields and item.status in TERMINAL:
            await mixes_done(s, [item])
        links = (await item_playlists(s, [item]))[item.id]
    bus.publish(item.user_id, "download", item_out(item, playlist_id=links).model_dump(mode="json"))
    return item


# --- catalog meta ----------------------------------------------------------------------------


def meta_from_catalog(t: CatalogTrack, album: CatalogAlbum | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {
        "track_ref": t.ref,
        "artists": t.artists or [t.artist],
        "album": t.album_title,
        "album_artist": t.album_artist,
        "track_no": t.track_no,
        "disc_no": t.disc_no,
        "isrc": t.isrc,
        "explicit": t.explicit,
        "release_date": t.release_date.isoformat() if t.release_date else None,
        "cover_url": t.cover_url,
        "duration_s": t.duration_s,
    }
    if t.source == "dz":
        m["deezer_track_id"] = t.id
        if t.album_ref:
            m["deezer_album_id"] = split_ref(t.album_ref)[1]
    if album is not None:
        m.update(
            album=album.title,
            album_artist=album.artist,
            record_type=album.record_type,
            release_date=album.release_date.isoformat() if album.release_date else m.get("release_date"),
            year=album.year,
            cover_url=album.cover_url or m.get("cover_url"),
            genre=album.genre,
            upc=album.upc,
            track_total=album.nb_tracks or (len(album.tracks) if album.tracks else None),
        )
        if album.source == "dz":
            m["deezer_album_id"] = album.id
    return {k: v for k, v in m.items() if v is not None}


async def complete_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Fill album details (track number, cover, release type...) for a Deezer track known only by id."""
    if not meta.get("deezer_track_id") or (meta.get("track_no") and meta.get("record_type")) or svc.deezer is None:
        return meta
    try:
        t = await svc.deezer.get_track(meta["deezer_track_id"])
        album = await svc.deezer.get_album(split_ref(t.album_ref)[1]) if t.album_ref else None
    except Exception as e:
        log.warning("deezer meta for %s failed: %r", meta.get("deezer_track_id"), e)
        return meta
    if album and album.tracks:
        hit = next((x for x in album.tracks if x.id == t.id), None)
        if hit:
            t = t.model_copy(update={"track_no": hit.track_no or t.track_no, "disc_no": hit.disc_no or t.disc_no})
    merged = meta_from_catalog(t, album)
    merged.update({k: v for k, v in meta.items() if v is not None and k not in ("track_no", "disc_no")})
    for k in ("track_no", "disc_no"):
        merged[k] = merged.get(k) or meta.get(k)
    return merged


async def resolve_catalog_meta(q: TrackQuery) -> dict[str, Any] | None:
    """Find the track in Deezer; only a confident match is used (download needs a catalog record, PLAN 8.5.3)."""
    t = await resolve_catalog_track(q)
    return meta_from_catalog(t) if t else None


async def resolve_catalog_track(q: TrackQuery) -> CatalogTrack | None:
    if svc.deezer is None:
        return None
    try:
        found = await svc.deezer.search_tracks(q.artist, q.title, limit=10)
    except Exception as e:
        log.warning("deezer search failed: %r", e)
        return None
    if not found:
        return None
    scored = match_catalog(q.artist, q.title, found, album=q.album, duration=q.duration_s, version=q.version)
    cfg = get_settings().cfg.matching
    from app.matching.select import classify_scores

    if classify_scores([sc.total for _, sc in scored], cfg) != "auto":
        return None
    return scored[0][0]


# --- candidates ------------------------------------------------------------------------------


async def fetch_cover(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        async with make_client(timeout=30) as c:
            r = await c.get(url)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                return r.content
    except httpx.HTTPError as e:
        log.warning("cover download failed %s: %r", url, e)
    return None


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# --- handlers ----------------------------------------------------------------------------------


@handler("download_item")
async def handle_download_item(job: Job) -> None:
    item_id = int(job.payload_json["item_id"])
    async with session_scope() as s:
        item = await s.get(DownloadItem, item_id)
        if item is None or item.status in ("done", "cancelled", "importing"):
            return
        qj = dict(item.query_json or {})
        chosen = item.chosen_candidate_json
        force = item.force
        user_id = item.user_id
    q = TrackQuery(**qj["query"])
    meta: dict[str, Any] = dict(qj.get("meta") or {})

    await update_item(item_id, status="searching", progress=0, error=None)

    # 1. catalog record (needed for tags; without it only a user-picked candidate may be downloaded). A picked
    # re-upload is looked up too: names from its title ("Трек – Исполнитель (без цензуры)") make poor tags.
    if not meta and not chosen:
        meta = await resolve_catalog_meta(q) or {}
    elif not meta and chosen is not None and (chosen.get("extra") or {}).get("reupload"):
        if t := await resolve_catalog_track(q):
            q, meta = query_from_track(t), meta_from_catalog(t)
            qj["query"] = q.model_dump()
    meta = await complete_meta(meta) if meta else meta
    if meta:
        q = q.model_copy(
            update={
                "duration_s": q.duration_s or meta.get("duration_s"),
                "isrc": q.isrc or meta.get("isrc"),
                "explicit": q.explicit if q.explicit is not None else meta.get("explicit"),
            }
        )
        qj["meta"] = meta
        qj["query"] = q.model_dump()
        await update_item(item_id, query_json=qj)

    # 2. already in the library?
    if not force:
        async with session_scope() as s:
            existing = await find_existing(
                s,
                artist=q.artist,
                title=q.title,
                duration_s=q.duration_s,
                isrc=q.isrc,
                deezer_track_id=meta.get("deezer_track_id"),
            )
            existing_id = existing.id if existing else None
            other_file = bool(existing and chosen and existing.source_url != chosen.get("page_url"))
        if other_file:
            # the user picked another file of a song the library has (an uncensored re-upload of a censored copy):
            # it goes next to the old one as a new version (PLAN 8.6), and ♥ moves over to it
            force = True
            qj["replaces"] = existing_id
            await update_item(item_id, query_json=qj)
        elif existing_id:
            # the catalog already holds the track: commit the item at once
            async with session_scope() as s:
                item = await s.get(DownloadItem, item_id, with_for_update=True)
                track = await s.get(Track, existing_id)
                if item is not None and track is not None:
                    await catalog_commit(s, item, track)
                    await s.flush()
            await update_item(item_id, warnings=["Уже есть в библиотеке"])
            await committed_effects(item_id)
            notify()
            return

    # 3. Russian tracks listed in Latin (Deezer translit): Cyrillic originals for the search, Russian source order
    pq = q
    if not chosen:
        q, pq = await russian_original(q, meta.get("upc"))
        if pq is not q:
            qj["query"], qj["provider_query"] = q.model_dump(), pq.model_dump()
            await update_item(item_id, query_json=qj)

    # 4. providers in order: search → rank → try the best files; the next provider is asked only if needed.
    # An explicit Russian track takes only copies known to be uncensored first (from the sources, then re-uploads
    # by small channels); a possibly censored copy is the last resort.
    stage = staging_dir_for("dl", item_id)
    errors: list[str] = []
    all_cands: list[Candidate] = []
    last_reason = ""
    providers = svc.provider_order(pq, for_download=True)
    mcfg = get_settings().cfg.matching
    guard = bool(meta) and not chosen and needs_uncensored(pq)
    held: list[Candidate] = []
    try:
        if chosen:
            ok, last_reason = await try_candidates(item_id, user_id, q, meta, [Candidate(**chosen)], stage, force)
            if ok:
                return
        else:
            # Soulseek first: it has lossless files the regular sources lack (`soulseek.format`, FLAC by default)
            if meta and (cands := await soulseek_candidates(pq, item_id)):
                all_cands.extend(cands)
                tries = [c for c in cands if not guard or trusted_original(c, mcfg)][:MAX_TRIES_PER_PROVIDER]
                if tries:
                    ok, last_reason = await try_candidates(item_id, user_id, q, meta, tries, stage, force)
                    if ok:
                        return
            from app.listen import listened_candidate

            # the file the user has just listened to (GET /api/listen) goes first: they get what they heard
            heard = await listened_candidate(meta.get("track_ref"))
            if heard and meta and (not guard or trusted_original(heard, mcfg)):
                ok, last_reason = await try_candidates(item_id, user_id, q, meta, [heard], stage, force)
                if ok:
                    return
            for p in providers:
                cands, err = await search_provider(p, pq, item_id)
                if err:
                    errors.append(err)
                    continue
                all_cands.extend(cands)
                await update_item(item_id, candidates_json=[c.model_dump(mode="json") for c in all_cands[:MAX_SHOWN]])
                if not meta or not cands:
                    continue  # without a catalog record we only collect candidates for the user (PLAN 8.5.3)
                tries = cands[:MAX_TRIES_PER_PROVIDER]
                if guard:
                    held += [c for c in tries if not trusted_original(c, mcfg)]
                    tries = [c for c in tries if trusted_original(c, mcfg)]
                    if not tries:
                        continue
                ok, reason = await try_candidates(item_id, user_id, q, meta, tries, stage, force)
                if ok:
                    return
                last_reason = reason or last_reason
            if guard:
                if reups := await search_reuploads(pq, item_id, searched=providers):
                    shown = all_cands[: MAX_SHOWN - min(MAX_SHOWN_REUPLOADS, len(reups))] + reups[:MAX_SHOWN_REUPLOADS]
                    all_cands.extend(reups)
                    await update_item(item_id, candidates_json=[c.model_dump(mode="json") for c in shown])
                    ok, reason = await try_candidates(
                        item_id, user_id, q, meta, reups[:MAX_TRIES_PER_PROVIDER], stage, force
                    )
                    if ok:
                        return
                    last_reason = reason or last_reason
                if held:
                    ok, reason = await try_candidates(
                        item_id, user_id, q, meta, held, stage, force, warning=MAYBE_CENSORED
                    )
                    if ok:
                        return
                    last_reason = reason or last_reason
    finally:
        cleanup_dir(stage)

    if chosen:
        await update_item(item_id, status="needs_review", error=f"Файл не подошёл ({last_reason})")
    elif not all_cands:
        if errors and len(errors) >= len(providers):
            raise RuntimeError("все источники недоступны: " + "; ".join(errors))
        await update_item(item_id, status="needs_review", error="Не найдено ни в одном источнике")
    elif not meta:
        sure = classify(all_cands, get_settings().cfg.matching) == "auto"
        await update_item(
            item_id,
            status="needs_review",
            error="Нет записи в каталоге — " + ("подтвердите файл" if sure else "выберите файл"),
        )
    else:
        await update_item(item_id, status="needs_review", error=f"Ни один кандидат не подошёл ({last_reason})")


async def soulseek_candidates(q: TrackQuery, item_id: int) -> list[Candidate]:
    """Soulseek files of the track when that source is on and logged in; otherwise none, and the download goes on
    with the regular sources at once (Soulseek is never waited for)."""
    p = svc.providers.get("soulseek")
    if p is None:
        return []
    health = getattr(p, "health", None)
    if health is not None and (state := await health()) != "ready":
        log.info("download %s: soulseek skipped (%s)", item_id, state)
        return []
    cands, err = await search_provider(p, q, item_id)
    if err:
        log.info("download %s: %s", item_id, err)
    return cands


async def search_provider(
    p, q: TrackQuery, item_id: int, *, limit: int = 15, enrich_top_n: int | None = None
) -> tuple[list[Candidate], str | None]:
    """Search one provider, rank its results and enrich the top ones (hitmoz: Explicit, real bitrate).
    Listening asks for fewer results and enrichments (`limit`, `enrich_top_n`): it has to start fast.
    Files uploaded after the source's cutoff (`sources.uploaded_before`) are dropped."""
    cfg = get_settings().cfg.matching
    cutoffs = get_settings().cfg.sources.uploaded_before
    n_enrich = cfg.enrich_top_n if enrich_top_n is None else enrich_top_n
    try:
        raw = await p.search(q, limit=limit)
    except ProviderError as e:
        return [], f"{p.name}: {e}"
    except Exception as e:
        log.warning("provider %s search failed: %r", p.name, e, extra={"item_id": item_id})
        return [], f"{p.name}: {e!r}"
    ranked = rank_candidates(q, [c for c in raw if uploaded_in_time(c, cutoffs)], cfg)
    if ranked and n_enrich:
        top = ranked[:n_enrich]
        enriched = await asyncio.gather(*(p.enrich(c) for c in top), return_exceptions=True)
        top = [e if isinstance(e, Candidate) else c for c, e in zip(top, enriched, strict=True)]
        # the track page may tell the upload date the search didn't
        ranked = rank_candidates(q, [c for c in top if uploaded_in_time(c, cutoffs)] + ranked[n_enrich:], cfg)
    return ranked, None


async def search_reuploads(q: TrackQuery, item_id: int, *, searched: list | None = None) -> list[Candidate]:
    """Re-uploads by small channels known to be uncensored (titled so, or uploaded before the law), best first.
    Sources in `searched` (the regular order already had them) are not asked again."""
    cfg = get_settings().cfg.matching
    providers = [p for p in svc.reupload_providers() if p not in (searched or [])]
    found = await asyncio.gather(*(search_provider(p, q, item_id) for p in providers))
    for _, err in found:
        if err:
            log.info("re-uploads: %s", err, extra={"item_id": item_id})
    return rank_candidates(q, [c for cands, _ in found for c in cands if trusted_original(c, cfg)], cfg)


async def try_candidates(
    item_id: int,
    user_id: int,
    q: TrackQuery,
    meta: dict[str, Any],
    cands: list[Candidate],
    stage: Path,
    force: bool,
    *,
    warning: str | None = None,
) -> tuple[bool, str]:
    """Download candidates one by one until one passes the post-download check and is imported. `warning` goes
    to the imported track's warnings."""
    pcfg = get_settings().cfg
    last_reason = ""
    for cand in cands:
        provider = svc.providers.get(cand.provider)
        if provider is None:
            continue
        await update_item(
            item_id,
            status="downloading",
            provider=cand.provider,
            chosen_candidate_json=cand.model_dump(mode="json"),
            progress=0,
        )
        last_pub = [0.0]
        loop = asyncio.get_running_loop()

        def on_progress(frac: float, _last=last_pub, _loop=loop) -> None:
            # may be called from a worker thread (yt-dlp)
            if frac - _last[0] >= 0.05 or frac >= 1:
                _last[0] = frac
                _loop.call_soon_threadsafe(lambda f=round(frac, 3): _loop.create_task(update_item(item_id, progress=f)))

        try:
            path = await provider.fetch(cand, stage, on_progress=on_progress)  # type: ignore[call-arg]
        except Exception as e:
            last_reason = f"{cand.provider}: {e}"
            log.warning("fetch failed %s: %r", cand.page_url, e, extra={"item_id": item_id})
            continue
        check = await probe.postcheck(
            path, q.duration_s or cand.duration_s, pcfg.matching.duration_tolerance_s, pcfg.postcheck.silence_warn_s
        )
        if not check.ok:
            last_reason = f"{cand.provider}: {check.reason}"
            path.unlink(missing_ok=True)
            continue
        await import_downloaded(item_id, user_id, q, meta, cand, path, check, force, warning=warning)
        return True, ""
    return False, last_reason


async def import_downloaded(
    item_id: int,
    user_id: int,
    q: TrackQuery,
    meta: dict[str, Any],
    cand: Candidate,
    path: Path,
    check: Any,
    force: bool,
    *,
    warning: str | None = None,
) -> None:
    await update_item(item_id, status="tagging", progress=1)
    cover = await fetch_cover(meta.get("cover_url") or cand.cover_url)
    release_date = _parse_date(meta.get("release_date"))
    single = not meta.get("album")
    spec = ImportSpec(
        artist=q.artist,
        title=q.title,
        artists=meta.get("artists") or [],
        album=meta.get("album") or q.title,
        album_artist=meta.get("album_artist") or q.artist,
        year=meta.get("year") or (release_date.year if release_date else cand.year),
        release_date=release_date,
        release_type={"compile": "compilation"}.get(meta.get("record_type", ""), meta.get("record_type"))
        or ("single" if single else "album"),
        track_no=meta.get("track_no") or (1 if single else None),
        track_total=meta.get("track_total"),
        disc_no=meta.get("disc_no") or 1,
        genre=meta.get("genre"),
        isrc=meta.get("isrc") or q.isrc,
        explicit=meta.get("explicit") if meta.get("explicit") is not None else cand.explicit,
        deezer_track_id=meta.get("deezer_track_id"),
        deezer_album_id=meta.get("deezer_album_id"),
        upc=meta.get("upc"),
        cover=cover,
        source=cand.provider,
        source_url=cand.page_url,
        source_upload_date=cand.upload_date,
        duration_s=round(check.info.duration_s) if check.info and check.info.duration_s else q.duration_s,
        bitrate_kbps=check.info.bitrate_kbps if check.info else cand.bitrate_kbps,
        codec=check.info.codec if check.info else None,
        warnings=list(check.warnings or []) + ([warning] if warning else []),
        added_by=user_id,
        force=force,
    )
    await update_item(item_id, status="importing")
    # one transaction: the committed blob, the track row and the item's `done` with its public uid
    async with session_scope() as s:
        track = await import_file(s, path, spec)
        item = await s.get(DownloadItem, item_id, with_for_update=True)
        if item is None:
            raise ValueError(f"элемент загрузки {item_id} исчез во время импорта")
        await catalog_commit(s, item, track)
        track_id, track_uid, album_id = track.id, track.uid, track.album_id
    await update_item(item_id, warnings=spec.warnings, error=None)
    bus.publish(
        user_id,
        "library",
        {"kind": "imported", "track_id": track_id, "uid": track_uid, "album_id": album_id},
    )
    if meta.get("track_ref"):
        from app.listen import forget

        await forget(meta["track_ref"])  # from now on listening plays the library file
    await committed_effects(item_id)
    notify()


@on_final_failure("download_item")
async def _download_failed(job: Job, exc: Exception) -> None:
    # playlist additions are deferred to stage 5: a failed item only reports itself
    await update_item(int(job.payload_json["item_id"]), status="failed", error=f"{exc}"[:500])


# --- the Hub-side effects of a committed item ---------------------------------------------------------------


async def committed_effects(item_id: int) -> None:
    """A committed item's Hub-side effects: its import's playlists are rewritten, its mix is synced soon."""
    async with session_scope() as s:
        item = await s.get(DownloadItem, item_id)
        if item is not None:
            await imports_done(s, [item])
            await mixes_done(s, [item])


async def imports_done(s: AsyncSession, items: list[DownloadItem]) -> None:
    if any((i.query_json or {}).get("import") for i in items):
        from app.imports.core import downloads_done

        await downloads_done(s, items)


async def mixes_done(s: AsyncSession, items: list[DownloadItem]) -> None:
    if any((i.query_json or {}).get("mix") for i in items):
        from app.mixes.core import downloads_changed

        await downloads_changed(s, items)


async def playlist_adds_loop() -> None:
    """Recover pending additions on startup and retry failed additions without another download."""
    while True:
        try:
            async with session_scope() as s:
                await process_playlist_adds(s)
        except Exception:
            log.exception("pending playlist additions failed")
        await asyncio.sleep(30)


async def process_playlist_adds(s: AsyncSession) -> None:
    """Wait for active downloads, pass manual review, and insert a reviewed song back into its requested place."""
    from app.playlists import add_tracks, get_by_uid

    # Serialize the readers through the transaction commit, including concurrent scan/download completions.
    await s.execute(select(func.pg_advisory_xact_lock(PLAYLIST_ADDS_LOCK_KEY)))
    rows = (
        await s.execute(
            select(PendingPlaylistAdd, DownloadItem, Track)
            .join(DownloadItem, DownloadItem.id == PendingPlaylistAdd.download_item_id)
            .outerjoin(Track, Track.id == DownloadItem.track_id)
            .order_by(
                PendingPlaylistAdd.playlist_uid,
                DownloadItem.batch_id,
                PendingPlaylistAdd.position,
                PendingPlaylistAdd.id,
            )
        )
    ).all()
    groups: dict[tuple[int, str], list[tuple[PendingPlaylistAdd, DownloadItem, Track | None]]] = {}
    for add, item, track in rows:
        groups.setdefault((add.user_id, add.playlist_uid), []).append((add, item, track))
    for (user_id, playlist_uid), entries in groups.items():
        playlist = await get_by_uid(s, playlist_uid)
        if playlist is None:
            for add, _item, _track in entries:
                await s.delete(add)
            continue
        placed: dict[int, dict[int, str]] = {}  # batch -> {position: inserted item uid}
        for add, item, track in entries:
            if item.status in ("failed", "cancelled"):
                await s.delete(add)
                continue
            if item.status == "needs_review":
                continue  # a manual decision does not hold ready songs hostage
            if track is None:
                break  # active downloads still finish in order
            done = placed.setdefault(item.batch_id, {})
            before = [p for p in done if p < add.position]
            after = [p for p in done if p > add.position]
            anchor = done[max(before)] if before else (None if after else add.anchor_uid)
            before_uid = done[min(after)] if after and not before else add.before_uid
            created = await add_tracks(
                s, playlist, [track.uid], anchor_uid=anchor, before_uid=before_uid, added_by=user_id
            )
            done[add.position] = created[0].uid
            # the next add of this batch goes after it, even across a restart
            nxt = [
                a for a, i, _t in entries if i.batch_id == item.batch_id and a.position > add.position and a is not add
            ]
            if nxt:
                min(nxt, key=lambda a: a.position).anchor_uid = created[0].uid
            # an earlier row still waiting (a manual decision) comes back before this one, not to the end
            for earlier in (
                a for a, i, _t in entries if i.batch_id == item.batch_id and a.position < add.position and a is not add
            ):
                if earlier.before_uid is None:
                    earlier.before_uid = created[0].uid
                    earlier.anchor_uid = None
            await s.delete(add)
    await s.flush()


# --- batch expansion ------------------------------------------------------------------------------


async def create_items(
    s: AsyncSession,
    batch: DownloadBatch,
    entries: list[tuple[TrackQuery, dict[str, Any], Candidate | None]],
    *,
    force: bool = False,
    star: bool = False,
    start_position: int = 0,
    favorite_events: dict[str, str] | None = None,
    anchor_uid: str | None = None,
) -> int:
    for i, (q, meta, cand) in enumerate(entries):
        query_json: dict[str, Any] = {"query": q.model_dump(), "meta": meta}
        if star:
            query_json["star"] = (
                True  # ♥: the local journal already holds the decision (`favorites.download_requested`)
            )
            if batch.origin == "assistant":
                query_json.update(
                    favorite_client="assistant", favorite_context="assistant:bulk" if len(entries) > 1 else "assistant"
                )
        item = DownloadItem(
            batch_id=batch.id,
            user_id=batch.user_id,
            query_json=query_json,
            chosen_candidate_json=cand.model_dump(mode="json") if cand else None,
            force=force,
        )
        s.add(item)
        await s.flush()
        if star:
            from app.favorites import download_requested, snapshot

            ref = item_ref(item)
            if favorite_events and ref in favorite_events:
                current = await snapshot(s, batch.user_id)
                state = next((x for x in current.values() if x.ref == ref and x.target_type == "track"), None)
                item.query_json = {
                    **item.query_json,
                    "star_event_id": favorite_events[ref],
                    "star": bool(state and state.liked),
                    "star_cancelled": not bool(state and state.liked),
                }
                if state is None or not state.liked:
                    # This quiet download was cancelled before expansion finished.
                    item.status = "cancelled"
            else:
                await download_requested(s, item)
        if batch.target_playlist_id:
            s.add(
                PendingPlaylistAdd(
                    playlist_uid=batch.target_playlist_id,
                    anchor_uid=anchor_uid,
                    user_id=batch.user_id,
                    download_item_id=item.id,
                    position=start_position + i,
                )
            )
        if item.status != "cancelled":
            await enqueue(s, "download_item", {"item_id": item.id})
    return len(entries)


def query_from_track(t: CatalogTrack) -> TrackQuery:
    return TrackQuery(
        artist=t.artist, title=t.title, album=t.album_title, duration_s=t.duration_s, isrc=t.isrc, explicit=t.explicit
    )


async def get_catalog_album(ref: str) -> CatalogAlbum:
    src, id_ = split_ref(ref)
    if src == "dz" and svc.deezer:
        return await svc.deezer.get_album(id_)
    if src == "hm" and svc.hitmoz_catalog:
        return await svc.hitmoz_catalog.get_album(id_)
    raise PermanentError(f"unknown album ref {ref}")


@handler("expand_batch")
async def handle_expand_batch(job: Job) -> None:
    p = job.payload_json
    album_refs: list[str] = list(p.get("album_refs") or [])
    if p.get("artist_ref"):
        src, id_ = split_ref(p["artist_ref"])
        types = set(p.get("record_types") or ["album", "single", "ep"])
        if src == "dz":
            if svc.deezer is None:
                return
            page = await svc.deezer.get_artist_page(id_)
        else:
            if svc.hitmoz_catalog is None:
                return
            page = await svc.hitmoz_catalog.get_artist(id_)
        # oldest first, so singles that are later included in an album don't win the album slot
        album_refs += [
            a.ref for a in sorted(page.albums, key=lambda a: a.release_date or date.min) if a.record_type in types
        ]
    entries: list[tuple[TrackQuery, dict[str, Any], Candidate | None]] = []
    seen: set[str] = set()
    for ref in album_refs:
        try:
            album = await get_catalog_album(ref)
        except (CatalogNotFound, PermanentError) as e:  # one dead ref must not sink the whole batch
            log.warning("expand_batch %s: skip album %s: %r", p["batch_id"], ref, e)
            continue
        for t in album.tracks or []:
            key = t.isrc or f"{t.artist}|{t.title}".casefold()
            if key in seen:
                continue
            seen.add(key)
            entries.append((query_from_track(t), meta_from_catalog(t, album), None))
    for ref in p.get("track_refs") or []:
        src, id_ = split_ref(ref)
        if src != "dz" or svc.deezer is None:
            continue
        try:
            t = await svc.deezer.get_track(id_)
        except CatalogNotFound as e:
            log.warning("expand_batch %s: skip track %s: %r", p["batch_id"], ref, e)
            continue
        entries.append((query_from_track(t), meta_from_catalog(t), None))
    async with session_scope() as s:
        batch = await s.get(DownloadBatch, int(p["batch_id"]))
        if batch is None:
            return
        await create_items(
            s,
            batch,
            entries,
            force=bool(p.get("force")),
            star=bool(p.get("star")),
            start_position=int(p.get("position", 0)),
            favorite_events=p.get("favorite_events"),
            anchor_uid=p.get("anchor_uid"),
        )
    notify()
    bus.publish(batch.user_id, "batch", {"batch_id": batch.id, "queued": len(entries)})
