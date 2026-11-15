"""decommission: drop the Navidrome columns of the transitional layer

Revision ID: i9e6f2d4b8c3
Revises: h8d5e1c3a7b2
Create Date: 2026-12-17 12:00:00.000000

Stage 7 (`navidrome-migration-7-decommission`): the Hub is the only source of the catalog, accounts, ♥, playlists and
history, so every `nd_*` alias of the transitional layer goes:

- `tracks.nd_song_id`, `play_events.nd_song_id`/`nd_album_id`/`nd_artist_id`, `dislikes.nd_song_id`,
  `mix_items.nd_song_id`, `smart_items.nd_song_id`, `smart_hidden.nd_song_id`, `wave_items.nd_song_id`;
- `wave_items.key` (it always repeated `ref`, which the analytics read);
- `mixes.nd_playlist_id`, `mix_settings.nd_playlist_id`,
  `import_collections.nd_playlist_id`/`nd_song_ids`;
- `favorite_subjects.nd_id`, `users.nd_username`/`nd_password_enc`;
- `play_events.mirror_pending` (nothing is mirrored to an external server);
- the indexes over the dropped columns.

`navidrome_sync` was dropped by stage 5 and is not touched here; `wave_sessions.seed_json` and
`play_events.superseded_scrobble` stay.

Downgrade note: the columns come back nullable and empty (no unique constraints: the identity they held is gone), so
old code starts, finds no links and treats every track as not yet linked. `wave_items.key` is restored as `ref`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "i9e6f2d4b8c3"
down_revision: str | None = "h8d5e1c3a7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, type) of every dropped column: the downgrade restores exactly this, nullable and empty
_DROPPED: tuple[tuple[str, str, sa.types.TypeEngine], ...] = (
    ("tracks", "nd_song_id", sa.String(length=64)),
    ("play_events", "nd_song_id", sa.String(length=64)),
    ("play_events", "nd_album_id", sa.String(length=64)),
    ("play_events", "nd_artist_id", sa.String(length=64)),
    ("play_events", "mirror_pending", sa.Boolean()),
    ("dislikes", "nd_song_id", sa.String(length=64)),
    ("mix_items", "nd_song_id", sa.String(length=64)),
    ("smart_items", "nd_song_id", sa.String(length=64)),
    ("smart_hidden", "nd_song_id", sa.String(length=64)),
    ("wave_items", "nd_song_id", sa.String(length=64)),
    ("wave_items", "key", sa.String(length=64)),
    ("mixes", "nd_playlist_id", sa.String(length=64)),
    ("mix_settings", "nd_playlist_id", sa.String(length=64)),
    ("import_collections", "nd_playlist_id", sa.String(length=64)),
    ("import_collections", "nd_song_ids", postgresql.JSONB()),
    ("favorite_subjects", "nd_id", sa.String(length=64)),
    ("users", "nd_username", sa.String(length=128)),
    ("users", "nd_password_enc", sa.Text()),
)

# (index name, table, columns): the downgrade recreates them as they were
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_tracks_nd_song_id", "tracks", ("nd_song_id",)),
    ("ix_play_events_song", "play_events", ("nd_song_id",)),
    ("ix_play_events_nd_album_id", "play_events", ("nd_album_id",)),
    ("ix_play_events_nd_artist_id", "play_events", ("nd_artist_id",)),
    ("ix_mix_items_nd_song_id", "mix_items", ("nd_song_id",)),
    ("ix_favorite_subjects_nd_id", "favorite_subjects", ("nd_id",)),
)


def upgrade() -> None:
    for name, _table, _columns in _INDEXES:
        op.drop_index(name, if_exists=True)
    # `users.nd_username` was unique: the constraint goes with its index
    op.drop_constraint("users_nd_username_key", "users", type_="unique", if_exists=True)
    for table, column, _type in _DROPPED:
        op.drop_column(table, column, if_exists=True)


def downgrade() -> None:
    for table, column, type_ in _DROPPED:
        op.add_column(table, sa.Column(column, type_, nullable=True))
    # `wave_items.key` always repeated `ref`
    op.execute("UPDATE wave_items SET key = ref WHERE key IS NULL")
    for name, table, columns in _INDEXES:
        op.create_index(name, table, list(columns), if_not_exists=True)
