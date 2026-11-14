"""blob storage: blobs table, blob links, logical paths and the trash marker

Revision ID: c3d1a7e5f9b2
Revises: b1f7c2d94e30
Create Date: 2026-11-13 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d1a7e5f9b2"
down_revision: str | None = "b1f7c2d94e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest_key", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_blobs_sha256"), "blobs", ["sha256"], unique=True)
    op.create_index(op.f("ix_blobs_status"), "blobs", ["status"], unique=False)

    op.add_column("albums", sa.Column("cover_blob_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_albums_cover_blob_id_blobs", "albums", "blobs", ["cover_blob_id"], ["id"], ondelete="SET NULL"
    )

    op.add_column("tracks", sa.Column("blob_id", sa.Integer(), nullable=True))
    op.add_column("tracks", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_tracks_blob_id_blobs", "tracks", "blobs", ["blob_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_tracks_blob_id"), "tracks", ["blob_id"], unique=False)
    op.create_index(op.f("ix_tracks_deleted_at"), "tracks", ["deleted_at"], unique=False)

    # a logical path is not the source of truth any more: the same bytes may live under several logical paths
    op.alter_column("tracks", "path", existing_type=sa.String(length=1024), nullable=True)
    op.drop_constraint("tracks_path_key", "tracks", type_="unique")
    op.create_index(op.f("ix_tracks_path"), "tracks", ["path"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tracks_path"), table_name="tracks")
    # the old schema had unique paths; duplicates appear only for shared blobs, which this stage introduces
    op.create_unique_constraint("tracks_path_key", "tracks", ["path"])
    op.alter_column("tracks", "path", existing_type=sa.String(length=1024), nullable=False)
    op.drop_index(op.f("ix_tracks_deleted_at"), table_name="tracks")
    op.drop_index(op.f("ix_tracks_blob_id"), table_name="tracks")
    op.drop_constraint("fk_tracks_blob_id_blobs", "tracks", type_="foreignkey")
    op.drop_column("tracks", "deleted_at")
    op.drop_column("tracks", "blob_id")
    op.drop_constraint("fk_albums_cover_blob_id_blobs", "albums", type_="foreignkey")
    op.drop_column("albums", "cover_blob_id")
    op.drop_index(op.f("ix_blobs_status"), table_name="blobs")
    op.drop_index(op.f("ix_blobs_sha256"), table_name="blobs")
    op.drop_table("blobs")
