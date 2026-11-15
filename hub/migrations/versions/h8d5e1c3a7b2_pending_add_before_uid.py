"""pending playlist adds: the successor an earlier reviewed item must precede

Revision ID: h8d5e1c3a7b2
Revises: g7c4d0b2f9a1
Create Date: 2026-12-16 16:00:00.000000

A batch can have a row waiting for a manual decision before an already inserted one; recalling the successor lets the
reviewed track come back to its place instead of the end of the playlist.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h8d5e1c3a7b2"
down_revision: str | None = "g7c4d0b2f9a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pending_playlist_adds", sa.Column("before_uid", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("pending_playlist_adds", "before_uid")
