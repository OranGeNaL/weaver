"""Schema of the Hub in Postgres (PLAN.md section 7): JSONB, timestamptz, JSONB-friendly defaults."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Now in UTC, timezone-aware: the schema stores timestamptz."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # the local account name (`username`): the Hub owns accounts
    username: Mapped[str] = mapped_column(String(128), unique=True)
    # argon2id; empty for a sign-in-only-by-OIDC account
    password_hash: Mapped[str | None] = mapped_column(Text)
    # set by bootstrap-by-token and by the CLI reset: everything but login/refresh/logout/me/password answers 403
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Google account linked to this user ("sub" of its ID token); moved into `oauth_links` by the stage-4
    # migration and kept only for the transition
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    google_email: Mapped[str | None] = mapped_column(String(320))
    # the confirmed email the account registered with (lower case): sign-in by it, too
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SearchHistory(Base):
    """The account's recent queries, shared by browsers and the embedded app."""

    __tablename__ = "search_history"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    query_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    query: Mapped[str] = mapped_column(String(512))
    searched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssistantDailyUsage(Base):
    """Persistent message allowance, independent of saved chats and LLM token usage."""

    __tablename__ = "assistant_daily_usage"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    messages: Mapped[int] = mapped_column(Integer)


class Invite(Base):
    """One-time registration code the admin hands to a person (FR-1: accounts only by invitation)."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)  # normalized: upper case, no dashes
    note: Mapped[str] = mapped_column(String(128), default="")  # who it is for
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class EmailCode(Base):
    """A code sent to an email for registration without an invite: whoever reads the letter owns the address."""

    __tablename__ = "email_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)  # lower case
    code_hash: Mapped[str] = mapped_column(String(64))  # HMAC of the email and the code
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)  # wrong codes typed
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiUsage(Base):
    """Every paid AI call of a user (chat turn, screenshot/link/text recognition): the admin's spending report."""

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # chat/images/link/text
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RefreshToken(Base):
    """One rotating refresh token of a device (`family_id` is the session, `device_id` the HttpOnly cookie).

    `id` is the SHA-256 of the opaque token. A rotated row keeps the pair it was exchanged for in
    `replacement_enc` (Fernet JSON) for the idempotency window: a parallel refresh with the same device gets the
    same pair instead of a second token.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_family", "family_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie token
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replacement_enc: Mapped[str | None] = mapped_column(Text)


class OAuthLink(Base):
    """An external OIDC identity (`iss` + `sub`) linked to a local account; never auto-linked by email."""

    __tablename__ = "oauth_links"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_oauth_links_issuer_subject"),
        Index("ix_oauth_links_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))  # google, generic provider name
    issuer: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LoginAttempt(Base):
    """A failed sign-in: the limiter counts them in Postgres, so it survives a restart and is shared by workers."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_name_time", "username", "attempted_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(128))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BootstrapState(Base):
    """The singleton row of the one-time admin bootstrap: `consumed_at` set, the token is spent for good."""

    __tablename__ = "bootstrap_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Blob(Base):
    """Immutable content-addressed object: one row per `sha256`, shared by every track with these bytes."""

    __tablename__ = "blobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str | None] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="audio")  # audio/covers
    # pending -> committed -> deleted; a committed row may still be shared by several tracks
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # `manifests/<sha256>/`: one JSON per track, because one blob may serve several tracks
    manifest_key: Mapped[str | None] = mapped_column(String(128))


class Artist(Base):
    """A lazily created artist: `name_norm` is the unique key, `tracks.artist`/`albums.album_artist` stay as credits."""

    __tablename__ = "artists"
    __table_args__ = (CheckConstraint("name_norm <> ''", name="ck_artists_name_norm_not_empty"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)
    name: Mapped[str] = mapped_column(String(512))
    # normalized name (app.matching.normalize.normalize_artist: latin form, else text): the deduplication key
    name_norm: Mapped[str] = mapped_column(String(512), unique=True)
    sort_name: Mapped[str] = mapped_column(String(512), default="", server_default="")
    cover_blob_id: Mapped[int | None] = mapped_column(ForeignKey("blobs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Album(Base):
    """Album as the Hub manages it. `uid` is written into files as HOMEMUSIC_ALBUM_ID."""

    __tablename__ = "albums"
    __table_args__ = (
        # nullable: NULL passes a CHECK; a set value is one of app.library.tagger.ReleaseType
        CheckConstraint("release_type IN ('album','single','ep','compilation')", name="ck_albums_release_type"),
        Index("ix_albums_artist_id", "artist_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)
    title: Mapped[str] = mapped_column(String(512))
    album_artist: Mapped[str] = mapped_column(String(512))
    artist_id: Mapped[int | None] = mapped_column(ForeignKey("artists.id", ondelete="SET NULL"))
    year: Mapped[int | None] = mapped_column(Integer)
    release_type: Mapped[str | None] = mapped_column(String(32))  # album/single/ep/compilation
    # catalog integrity of the cover: `missing_cover`, cleared by catalog_reconcile
    integrity_issue: Mapped[str | None] = mapped_column(String(32))
    # normalized "{album_artist} {title}" for the multi-token case-insensitive search (app.library.search_text)
    search_text: Mapped[str] = mapped_column(String(2048), default="", server_default="")
    deezer_album_id: Mapped[str | None] = mapped_column(String(32), index=True)
    upc: Mapped[str | None] = mapped_column(String(32))
    genre: Mapped[str | None] = mapped_column(String(128))
    cover_path: Mapped[str | None] = mapped_column(String(1024))  # logical album cover path (display/export)
    cover_blob_id: Mapped[int | None] = mapped_column(ForeignKey("blobs.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (Index("ix_tracks_fingerprint", "fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)  # HOMEMUSIC_TRACK_ID
    album_id: Mapped[int | None] = mapped_column(ForeignKey("albums.id", ondelete="SET NULL"), index=True)
    path: Mapped[str | None] = mapped_column(String(1024), index=True)  # logical library path (display/export)
    blob_id: Mapped[int | None] = mapped_column(ForeignKey("blobs.id", ondelete="SET NULL"), index=True)
    # trash: a deleted track keeps its row (and blob) until `library.trash_days` elapses
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # catalog integrity: `missing_blob`, cleared by catalog_reconcile
    integrity_issue: Mapped[str | None] = mapped_column(String(32))
    artist: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))
    album_artist: Mapped[str | None] = mapped_column(String(512))
    # normalized "{artist} {title} {album}" for the multi-token case-insensitive search; recomputed on tag edits
    search_text: Mapped[str] = mapped_column(String(2048), default="", server_default="")
    track_no: Mapped[int | None] = mapped_column(Integer)
    disc_no: Mapped[int | None] = mapped_column(Integer)
    year: Mapped[int | None] = mapped_column(Integer)
    genre: Mapped[str | None] = mapped_column(String(128))
    duration_s: Mapped[int | None] = mapped_column(Integer)
    bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    codec: Mapped[str | None] = mapped_column(String(16))
    isrc: Mapped[str | None] = mapped_column(String(16), index=True)
    deezer_track_id: Mapped[str | None] = mapped_column(String(32), index=True)
    explicit: Mapped[bool | None] = mapped_column(Boolean)
    source: Mapped[str | None] = mapped_column(String(32))  # hitmoz/hitmos_fm/ytdlp/upload/manual
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_upload_date: Mapped[date | None] = mapped_column(Date)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint: Mapped[str] = mapped_column(String(1024))  # normalized "artist|title"
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class CatalogCache(Base):
    __tablename__ = "catalog_cache"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ttl_s: Mapped[int] = mapped_column(Integer)


class DownloadBatch(Base):
    __tablename__ = "download_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(16))  # search/catalog/assistant
    title: Mapped[str | None] = mapped_column(String(512))
    target_playlist_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


ITEM_STATUSES = (
    "queued",
    "searching",
    "downloading",
    "tagging",
    "importing",
    "done",
    "failed",
    "needs_review",
    "cancelled",
)


class DownloadItem(Base):
    __tablename__ = "download_items"
    __table_args__ = (
        Index("ix_download_items_import_id", text("((query_json->'import'->>'id')::int)")),
        Index("ix_download_items_import_album", text("((query_json->'import'->>'album')::int)")),
        Index("ix_download_items_mix_id", text("((query_json->'mix'->>'id')::int)")),
        Index("ix_download_items_track_ref", text("(query_json->'meta'->>'track_ref')")),
        # idempotency key of `catalog_commit`: one item commits a blob once
        UniqueConstraint("id", "blob_sha256", name="uq_download_items_id_blob_sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("download_batches.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSONB)  # TrackQuery + catalog refs (deezer ids, album info)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    provider: Mapped[str | None] = mapped_column(String(32))
    chosen_candidate_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    candidates_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    # the bytes this item put into the catalog: its `(download_item_id, blob_sha256)` is the idempotency key
    blob_sha256: Mapped[str | None] = mapped_column(String(64))
    force: Mapped[bool] = mapped_column(Boolean, default=False)  # explicit re-download (new file next to locked one)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_pick", "status", "run_after"),
        # payload lookups: one queued build per user/mix/import, a download item's job
        Index("ix_jobs_payload_user_id", text("((payload_json->>'user_id')::int)")),
        Index("ix_jobs_payload_mix_id", text("((payload_json->>'mix_id')::int)")),
        Index("ix_jobs_payload_import_id", text("((payload_json->>'import_id')::int)")),
        Index("ix_jobs_payload_item_id", text("((payload_json->>'item_id')::int)")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/running/done/failed
    # higher first; imports run below 0 (`queue.PRIORITY_IMPORT`) and never take every worker
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    staging_path: Mapped[str] = mapped_column(String(1024))  # relative to staging/
    orig_name: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(Integer, default=0)
    probed_tags_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")  # uploaded/imported/failed/discarded
    error: Mapped[str | None] = mapped_column(Text)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssistantTask(Base):
    __tablename__ = "assistant_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    input_kind: Mapped[str] = mapped_column(String(16))  # images/link/text
    input_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # image paths / url / text
    extraction_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolution_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    # queued/extracting/resolving/awaiting_confirmation/confirmed/done/failed
    confirm_token: Mapped[str | None] = mapped_column(String(64))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("download_batches.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AssistantChat(Base):
    """A saved conversation with the assistant: the messages as the PWA shows them (no images, only their count)."""

    __tablename__ = "assistant_chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    # [{"role", "content", "images"?, "cost"?, "done"?, "proposal"?: {task_id, token, summary, actions, state}}]
    messages: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Playlist(Base):
    """A playlist of the Hub: the only owner of playlists since stage 5. `uid` is what the API shows."""

    __tablename__ = "playlists"
    __table_args__ = (Index("ix_playlists_owner", "owner_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(512))
    # «Микс дня» and other playlists the Hub builds: shown only to their owner
    system: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PlaylistItem(Base):
    """One position of a playlist. Duplicates are allowed; `anchor_uid` is the uid of the neighbouring item."""

    __tablename__ = "playlist_items"
    __table_args__ = (
        UniqueConstraint("playlist_id", "uid", name="uq_playlist_items_playlist_uid"),
        Index("ix_playlist_items_order", "playlist_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"))
    track_uid: Mapped[str] = mapped_column(ForeignKey("tracks.uid", ondelete="CASCADE"), index=True)
    # numeric so a background insert goes between two neighbours without rewriting every row
    position: Mapped[Decimal] = mapped_column(Numeric(30, 10))
    anchor_uid: Mapped[str | None] = mapped_column(String(36))
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PendingPlaylistAdd(Base):
    __tablename__ = "pending_playlist_adds"

    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_uid: Mapped[str] = mapped_column(String(36), index=True)
    anchor_uid: Mapped[str | None] = mapped_column(String(36))  # the item it goes after; None: the end
    before_uid: Mapped[str | None] = mapped_column(String(36))  # a later item of the batch it must precede
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    download_item_id: Mapped[int] = mapped_column(ForeignKey("download_items.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Import(Base):
    """Moving a user's collection over from a music service (Yandex Music now; Spotify, YouTube Music later)."""

    __tablename__ = "imports"
    # the sets of app.api.schemas (ImportSource, ImportMethod, ImportMode): the casts of the row into a Literal are
    # checked by Postgres, not by convention
    __table_args__ = (
        CheckConstraint("source IN ('yandex','spotify')", name="ck_imports_source"),
        CheckConstraint("method IN ('device','token','profile','file')", name="ck_imports_method"),
        CheckConstraint("mode IN ('economy','full')", name="ck_imports_mode"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # yandex
    method: Mapped[str] = mapped_column(String(16))  # device/token/profile
    # auth/fetching/preview/running/done/failed/cancelled
    status: Mapped[str] = mapped_column(String(16), default="fetching")
    mode: Mapped[str] = mapped_column(String(16), default="economy")  # economy/full
    account: Mapped[str | None] = mapped_column(String(256))  # login or profile shown to the user
    token_enc: Mapped[str | None] = mapped_column(Text)  # service token (Fernet)
    keep_token: Mapped[bool] = mapped_column(Boolean, default=False)  # kept after the import for «Обновить»
    auth_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # sign-in in progress (device code)
    options_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # download_albums...
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportCollection(Base):
    """«Мне нравится», a playlist, liked albums / artists, dislikes of an import."""

    __tablename__ = "import_collections"
    # app.api.schemas.CollectionKind
    __table_args__ = (
        CheckConstraint(
            "kind IN ('likes','playlist','albums','artists','dislikes')", name="ck_import_collections_kind"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # likes/playlist/albums/artists/dislikes
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(512))
    position: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    cover_url: Mapped[str | None] = mapped_column(String(1024))
    # the Hub playlist this collection became (source of truth since stage 5)
    playlist_uid: Mapped[str | None] = mapped_column(String(36))
    track_uids: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # what the Hub playlist holds now


class ImportItem(Base):
    """A track (or an album / artist) of an import collection, in the service's order."""

    __tablename__ = "import_items"
    __table_args__ = (Index("ix_import_items_ref", "ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"), index=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("import_collections.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    external_id: Mapped[str] = mapped_column(String(128))
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # artist, title, album, duration_s...
    ref: Mapped[str | None] = mapped_column(String(64))  # catalog track / album / artist
    match: Mapped[str | None] = mapped_column(String(16))  # owned/exact/probable/not_found
    score: Mapped[float | None] = mapped_column(Float)
    # pending/waiting/queued/done/failed/not_found/skipped
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    download_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("download_items.id", ondelete="SET NULL"), index=True
    )


class SourceMap(Base):
    """What an id of a music service turned out to be in the catalog: shared by every import of every user."""

    __tablename__ = "source_map"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)  # yandex
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)  # track/album/artist
    external_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ref: Mapped[str | None] = mapped_column(String(64))
    match: Mapped[str] = mapped_column(String(16))  # exact/probable/not_found
    score: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PlayEvent(Base):
    """One listening event of a user (MIXES.md 3.2): start/skip/finish of a track, ♥, «Не нравится»."""

    __tablename__ = "play_events"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_play_events_user_event"),
        Index("ix_play_events_user_ts", "user_id", "ts"),
        Index("ix_play_events_user_target_ts", "user_id", "target_type", "ts", "id"),
        Index("ix_play_events_playback", "user_id", "device_id", "playback_id"),
        Index("ix_play_events_user_launch", "user_id", "launch_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(String(64))  # the client's id: a resent event is not stored twice
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC
    kind: Mapped[str] = mapped_column(String(16))  # start/skip/finish/like/unlike/dislike/undislike
    ref: Mapped[str | None] = mapped_column(String(64))  # catalog track
    track_uid: Mapped[str | None] = mapped_column(String(36), index=True)  # the Hub track
    target_type: Mapped[str] = mapped_column(String(16), default="track", server_default="track")
    target_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reverts_event_id: Mapped[str | None] = mapped_column(String(64))
    playback_id: Mapped[str | None] = mapped_column(String(64))
    device_id: Mapped[str | None] = mapped_column(String(64))
    listened_ms: Mapped[int | None] = mapped_column()
    track_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    superseded_scrobble: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # a scrobble a Hub client sent (`POST /api/plays/scrobble`): an un-superseded one is a counted listen
    scrobble: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    origin: Mapped[str | None] = mapped_column(String(16))
    position_s: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[float | None] = mapped_column(Float)
    context: Mapped[str | None] = mapped_column(String(128))  # mix:<id>, wave:<session>, album, playlist, search...
    client: Mapped[str] = mapped_column(String(32))  # app/<version>, pwa
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # analytics (INSIGHTS.md 6.5): every event of one launched list shares `launch_id` ("wave:<sid>" for a wave);
    # `launch_origin` — where it was launched. The taste profile never reads them.
    launch_id: Mapped[str | None] = mapped_column(String(64))
    launch_origin: Mapped[str | None] = mapped_column(String(32))


class FavoriteSubject(Base):
    """Verified aliases and metadata, never the user's favorite state."""

    __tablename__ = "favorite_subjects"
    __table_args__ = (UniqueConstraint("target_type", "canonical_key", name="uq_favorite_subject_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16))
    canonical_key: Mapped[str] = mapped_column(String(128))
    ref: Mapped[str | None] = mapped_column(String(64), index=True)
    uid: Mapped[str | None] = mapped_column(String(36), index=True)  # the Hub track/album/artist uid
    aliases: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    enriched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Dislike(Base):
    """«Не нравится»: a track (`ref`) or an artist (`artist_ref`) the user doesn't want to hear."""

    __tablename__ = "dislikes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str | None] = mapped_column(String(64))
    artist_ref: Mapped[str | None] = mapped_column(String(64))
    artist: Mapped[str | None] = mapped_column(String(512))  # for showing the list
    title: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(16))  # app/pwa/yandex_import
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MixSettings(Base):
    """A user's «Микс дня» settings (MIXES.md 6): mode, the hour of the build, the playlist, onboarding artists."""

    __tablename__ = "mix_settings"
    # app.api.schemas.MixMode
    __table_args__ = (CheckConstraint("mode IN ('normal','favorites','discover')", name="ck_mix_settings_mode"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="normal")  # normal/favorites/discover
    mix_hour: Mapped[int] = mapped_column(Integer, default=5)  # local time (`Settings.timezone`)
    playlist_uid: Mapped[str | None] = mapped_column(String(36))  # «Микс дня» in the Hub: one uid for good
    # onboarding: [{ref, name}] the user picked on «Что вы слушаете?» (a new user without history)
    artists_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    last_build_on: Mapped[date | None] = mapped_column(Date)  # local date of the last scheduled build attempt
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Mix(Base):
    """One «Микс дня» of a user: lives until the next one (MIXES.md 6)."""

    __tablename__ = "mixes"
    # app.api.schemas.MixMode
    __table_args__ = (CheckConstraint("mode IN ('normal','favorites','discover')", name="ck_mixes_mode"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(16))
    playlist_uid: Mapped[str | None] = mapped_column(String(36))  # the Hub playlist of the mix
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/closed
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("download_batches.id", ondelete="SET NULL"))
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # generators, timings (the report)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MixItem(Base):
    """A song of a mix: one of the 50 (`state` "active") or the reserve that replaces one that did not download."""

    __tablename__ = "mix_items"
    __table_args__ = (Index("ix_mix_items_mix", "mix_id", "state", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mix_id: Mapped[int] = mapped_column(ForeignKey("mixes.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # order in the mix; the reserve after the 50, best first
    state: Mapped[str] = mapped_column(String(16), default="active")  # active/reserve/failed
    familiar: Mapped[bool] = mapped_column(Boolean, default=False)
    ref: Mapped[str | None] = mapped_column(String(64), index=True)  # catalog track
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"), index=True)
    generator: Mapped[str] = mapped_column(String(32))  # the strongest one: favorites/stash/ytm_radio/...
    score: Mapped[float] = mapped_column(Float, default=0)
    # the catalog track (`CatalogTrack`) and what scored it: generators, sim, popularity
    features_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    downloaded_for_mix: Mapped[bool] = mapped_column(Boolean, default=False)
    download_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("download_items.id", ondelete="SET NULL"), index=True
    )


class WaveSession(Base):
    """One «Моя волна» of a user (MIXES.md 7): the endless stream the Hub picks 5 tracks at a time. `state_json` —
    the pool of candidates, what was handed out and played, the session's skips and likes (`app.mixes.wave`)."""

    __tablename__ = "wave_sessions"
    # its `mode` is a mix's (app.api.schemas.MixMode)
    __table_args__ = (
        CheckConstraint("mode IN ('normal','favorites','discover')", name="ck_wave_sessions_mode"),
        Index("ix_wave_sessions_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    mode: Mapped[str] = mapped_column(String(16), default="normal")  # normal/favorites/discover, as a mix's
    seed_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {ref|artist_ref, title}: «по треку»
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("download_batches.id", ondelete="SET NULL"))
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # analytics (INSIGHTS.md 6.2–6.3): where the wave was started, by what client, the raw request; a session the
    # app made again after a 404 names the old one (`origin` "restart"). Never read by the wave itself.
    origin: Mapped[str | None] = mapped_column(String(32))
    client: Mapped[str | None] = mapped_column(String(32))
    restart_of: Mapped[str | None] = mapped_column(String(32))
    taste_model: Mapped[str | None] = mapped_column(String(8))
    request_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    next_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # an old session's heavy state (pool, queue, played) was replaced by a summary: the row itself is kept
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WaveItem(Base):
    """A track a wave handed out (INSIGHTS.md 6.4), once per session: analytics only, the wave never reads it."""

    __tablename__ = "wave_items"
    __table_args__ = (UniqueConstraint("session_id", "ref", name="uq_wave_items_session_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("wave_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    first_handed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_handed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    handed_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    position: Mapped[int] = mapped_column(Integer)  # the order of first hand-outs in the session, from 0
    ref: Mapped[str] = mapped_column(String(64))
    artist: Mapped[str] = mapped_column(String(256), default="", server_default="")
    title: Mapped[str] = mapped_column(String(256), default="", server_default="")
    generator: Mapped[str] = mapped_column(String(32))  # `wave.top_generator`
    gens: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # generator -> best position in its list
    familiar: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    sim: Mapped[float | None] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)  # `wave.value` the pick ranked it by (none: the seed)


class SmartSession(Base):
    """One run of the smart shuffle of a playlist or of «Понравившиеся» (MIXES.md 8): the order of the own songs
    and the recommendations the Hub put between them. `state_json` — what the session needs to go on after a
    restart (`app.mixes.smart`); the rest is for the analytics."""

    __tablename__ = "smart_sessions"
    __table_args__ = (Index("ix_smart_sessions_user_context", "user_id", "context", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    context: Mapped[str] = mapped_column(String(80))  # "liked" or "playlist:<uid>"
    title: Mapped[str | None] = mapped_column(String(256))  # the playlist's name as the client sent it
    own_count: Mapped[int] = mapped_column(Integer, default=0)
    per_rec: Mapped[int] = mapped_column(Integer, default=3)  # own songs per recommendation
    first_id: Mapped[str | None] = mapped_column(String(80))  # the song it started from (tapped or playing)
    taste_model: Mapped[str | None] = mapped_column(String(8))
    origin: Mapped[str | None] = mapped_column(String(32))
    client: Mapped[str | None] = mapped_column(String(32))
    next_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    state_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SmartItem(Base):
    """A recommendation a smart shuffle handed out, once per session (analytics; `state` follows the user)."""

    __tablename__ = "smart_items"
    __table_args__ = (UniqueConstraint("session_id", "ref", name="uq_smart_items_session_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("smart_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    handed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    position: Mapped[int] = mapped_column(Integer)  # the order of hand-outs in the session, from 0
    slot: Mapped[int] = mapped_column(Integer)  # which recommendation place of the list it took
    ref: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(1024), default="", server_default="")
    artist: Mapped[str] = mapped_column(String(256), default="", server_default="")
    title: Mapped[str] = mapped_column(String(256), default="", server_default="")
    generator: Mapped[str] = mapped_column(String(32))
    gens: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    familiar: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    sim: Mapped[float | None] = mapped_column(Float)  # closeness by sound to the playlist's center
    value: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(12), default="handed", server_default="handed")  # handed/added/hidden


class SmartHidden(Base):
    """«Не рекомендовать в этом плейлисте»: a track the user hid from the recommendations of one context. Not the
    global «Не нравится» (`Dislike`): the track stays everywhere else."""

    __tablename__ = "smart_hidden"
    __table_args__ = (UniqueConstraint("user_id", "context", "fingerprint", name="uq_smart_hidden"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    context: Mapped[str] = mapped_column(String(80))
    fingerprint: Mapped[str] = mapped_column(String(1024))
    ref: Mapped[str | None] = mapped_column(String(64))
    artist: Mapped[str] = mapped_column(String(256), default="", server_default="")
    title: Mapped[str] = mapped_column(String(256), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(Album, "before_insert")
@event.listens_for(Album, "before_update")
def _fill_album_search_text(_mapper, _connection, album: Album) -> None:
    """`albums.search_text` follows the tags: the search never normalizes in Python at query time."""
    from app.library.search import album_search_text

    album.search_text = album_search_text(album.album_artist, album.title)


@event.listens_for(Track, "before_insert")
@event.listens_for(Track, "before_update")
def _fill_track_search_text(_mapper, _connection, track: Track) -> None:
    from app.library.search import track_search_text

    track.search_text = track_search_text(track.artist, track.title, track.album)
