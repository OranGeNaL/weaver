"""import collections: Hub playlist uid and track uids

Revision ID: g7c4d0b2f9a1
Revises: f6b3c9a1d2e8
Create Date: 2026-12-16 15:00:00.000000

`import_collections` gets the Hub playlist it became (`playlist_uid`) and its current track uids
(`track_uids`). The deprecated `nd_playlist_id`/`nd_song_ids` keep the same values until stage 7.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "g7c4d0b2f9a1"
down_revision: str | None = "f6b3c9a1d2e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("import_collections", sa.Column("playlist_uid", sa.String(length=36), nullable=True))
    op.add_column(
        "import_collections",
        sa.Column("track_uids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    # development databases may already hold Hub playlist uids in the deprecated column
    op.execute("UPDATE import_collections SET playlist_uid = nd_playlist_id WHERE playlist_uid IS NULL")
    op.execute("UPDATE import_collections SET track_uids = nd_song_ids WHERE track_uids = '[]'::jsonb")


def downgrade() -> None:
    op.drop_column("import_collections", "track_uids")
    op.drop_column("import_collections", "playlist_uid")
