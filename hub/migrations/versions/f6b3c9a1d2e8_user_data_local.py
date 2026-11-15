"""user data: Hub playlists, scrobble markers, uid aliases; Navidrome sync removed

Revision ID: f6b3c9a1d2e8
Revises: e5a2b8d1c7f4
Create Date: 2026-12-16 12:00:00.000000

Stage 5 (`navidrome-migration-5-user-data`): ♥, playlists and listening history belong to the Hub.

- `playlists`/`playlist_items` with a `numeric` position and an insertion anchor;
- `mix_settings.playlist_uid`, `mixes.playlist_uid` (the `nd_*` columns stay as deprecated aliases until stage 7);
- `pending_playlist_adds.playlist_uid` + `anchor_uid`;
- `play_events.track_uid`, the `scrobble` marker and `origin`;
- `favorite_subjects.uid` (the uid alias of a ♥);
- `navidrome_sync` is dropped: nothing is imported from Navidrome any more.

Downgrade note: `pending_playlist_adds.applied_song_id` held Navidrome song ids and is restored empty, so a rollback
loses only that ND anchor (the Hub anchors are not meaningful to the old code).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b3c9a1d2e8"
down_revision: str | None = "e5a2b8d1c7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playlists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("system", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_playlists_owner_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid", name="uq_playlists_uid"),
    )
    op.create_index("ix_playlists_owner", "playlists", ["owner_id"], unique=False)

    op.create_table(
        "playlist_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.String(length=36), nullable=False),
        sa.Column("playlist_id", sa.Integer(), nullable=False),
        sa.Column("track_uid", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Numeric(30, 10), nullable=False),
        sa.Column("anchor_uid", sa.String(length=36), nullable=True),
        sa.Column("added_by", sa.Integer(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["playlist_id"], ["playlists.id"], name="fk_playlist_items_playlist_id_playlists", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["track_uid"], ["tracks.uid"], name="fk_playlist_items_track_uid_tracks", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["users.id"], name="fk_playlist_items_added_by_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid", name="uq_playlist_items_uid"),
        sa.UniqueConstraint("playlist_id", "uid", name="uq_playlist_items_playlist_uid"),
    )
    op.create_index("ix_playlist_items_track_uid", "playlist_items", ["track_uid"], unique=False)
    op.create_index("ix_playlist_items_order", "playlist_items", ["playlist_id", "position"], unique=False)

    op.add_column("mix_settings", sa.Column("playlist_uid", sa.String(length=36), nullable=True))
    op.add_column("mixes", sa.Column("playlist_uid", sa.String(length=36), nullable=True))

    # pending adds name a Hub playlist and the item they go next to instead of a Navidrome song
    op.add_column("pending_playlist_adds", sa.Column("playlist_uid", sa.String(length=36), nullable=True))
    op.add_column("pending_playlist_adds", sa.Column("anchor_uid", sa.String(length=36), nullable=True))
    op.execute("UPDATE pending_playlist_adds SET playlist_uid = playlist_id WHERE playlist_uid IS NULL")
    op.alter_column("pending_playlist_adds", "playlist_uid", nullable=False)
    op.create_index("ix_pending_playlist_adds_playlist_uid", "pending_playlist_adds", ["playlist_uid"], unique=False)
    op.drop_column("pending_playlist_adds", "applied_song_id")
    op.drop_column("pending_playlist_adds", "playlist_id")

    op.add_column("play_events", sa.Column("track_uid", sa.String(length=36), nullable=True))
    op.add_column("play_events", sa.Column("scrobble", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("play_events", sa.Column("origin", sa.String(length=16), nullable=True))
    op.create_index("ix_play_events_track_uid", "play_events", ["track_uid"], unique=False)

    op.add_column("favorite_subjects", sa.Column("uid", sa.String(length=36), nullable=True))
    op.create_index("ix_favorite_subjects_uid", "favorite_subjects", ["uid"], unique=False)

    op.drop_table("navidrome_sync")


def downgrade() -> None:
    op.create_table(
        "navidrome_sync",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("last_scrobble_id", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("favorites_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("favorites_bootstrapped_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_navidrome_sync_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.drop_index("ix_favorite_subjects_uid", table_name="favorite_subjects")
    op.drop_column("favorite_subjects", "uid")

    op.drop_index("ix_play_events_track_uid", table_name="play_events")
    op.drop_column("play_events", "origin")
    op.drop_column("play_events", "scrobble")
    op.drop_column("play_events", "track_uid")

    op.add_column("pending_playlist_adds", sa.Column("playlist_id", sa.String(length=64), nullable=True))
    op.add_column("pending_playlist_adds", sa.Column("applied_song_id", sa.String(length=64), nullable=True))
    op.execute("UPDATE pending_playlist_adds SET playlist_id = playlist_uid WHERE playlist_id IS NULL")
    op.alter_column("pending_playlist_adds", "playlist_id", nullable=False)
    op.create_index("ix_pending_playlist_adds_playlist_id", "pending_playlist_adds", ["playlist_id"], unique=False)
    op.drop_index("ix_pending_playlist_adds_playlist_uid", table_name="pending_playlist_adds")
    op.drop_column("pending_playlist_adds", "anchor_uid")
    op.drop_column("pending_playlist_adds", "playlist_uid")

    op.drop_column("mixes", "playlist_uid")
    op.drop_column("mix_settings", "playlist_uid")

    op.drop_index("ix_playlist_items_order", table_name="playlist_items")
    op.drop_index("ix_playlist_items_track_uid", table_name="playlist_items")
    op.drop_table("playlist_items")
    op.drop_index("ix_playlists_owner", table_name="playlists")
    op.drop_table("playlists")
