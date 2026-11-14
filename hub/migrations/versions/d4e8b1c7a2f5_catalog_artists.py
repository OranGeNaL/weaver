"""catalog: lazy artists, album artist link, integrity markers and the commit key

Revision ID: d4e8b1c7a2f5
Revises: c3d1a7e5f9b2
Create Date: 2026-11-14 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8b1c7a2f5"
down_revision: str | None = "c3d1a7e5f9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uid", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("name_norm", sa.String(length=512), nullable=False),
        sa.Column("sort_name", sa.String(length=512), server_default="", nullable=False),
        sa.Column("cover_blob_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("name_norm <> ''", name="ck_artists_name_norm_not_empty"),
        sa.ForeignKeyConstraint(
            ["cover_blob_id"], ["blobs.id"], name="fk_artists_cover_blob_id_blobs", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uid"),
        sa.UniqueConstraint("name_norm"),
    )

    op.add_column("albums", sa.Column("artist_id", sa.Integer(), nullable=True))
    op.add_column("albums", sa.Column("integrity_issue", sa.String(length=32), nullable=True))
    op.create_foreign_key(
        "fk_albums_artist_id_artists", "albums", "artists", ["artist_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_albums_artist_id", "albums", ["artist_id"], unique=False)

    op.add_column("tracks", sa.Column("integrity_issue", sa.String(length=32), nullable=True))

    op.add_column("download_items", sa.Column("blob_sha256", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_download_items_id_blob_sha256", "download_items", ["id", "blob_sha256"])


def downgrade() -> None:
    op.drop_constraint("uq_download_items_id_blob_sha256", "download_items", type_="unique")
    op.drop_column("download_items", "blob_sha256")

    op.drop_column("tracks", "integrity_issue")

    op.drop_index("ix_albums_artist_id", table_name="albums")
    op.drop_constraint("fk_albums_artist_id_artists", "albums", type_="foreignkey")
    op.drop_column("albums", "integrity_issue")
    op.drop_column("albums", "artist_id")

    op.drop_table("artists")
