"""optional pg_trgm: better fuzzy search, never fatal

Revision ID: b1f7c2d94e30
Revises: 4729f573f4ac
Create Date: 2026-11-13 15:10:00.000000

`CREATE EXTENSION` needs superuser rights on a hosted Postgres. The extension is an improvement, not a
requirement: without it the search degrades to `lower`/`ILIKE`. Hence the autocommit block and the caught error —
`upgrade head` must not fail here.
"""

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "b1f7c2d94e30"
down_revision: str | None = "4729f573f4ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    try:
        # autocommit: a failed CREATE EXTENSION must not abort the migration transaction
        with op.get_context().autocommit_block():
            op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_tracks_search_text_trgm ON tracks USING gin (search_text gin_trgm_ops)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_albums_search_text_trgm ON albums USING gin (search_text gin_trgm_ops)"
            )
    except Exception as exc:
        log.warning("pg_trgm недоступен, поиск работает через lower/ILIKE: %r", exc)


def downgrade() -> None:
    # the extension may be shared with other schemas; only the indexes are ours
    try:
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX IF EXISTS ix_tracks_search_text_trgm")
            op.execute("DROP INDEX IF EXISTS ix_albums_search_text_trgm")
    except Exception as exc:
        log.warning("pg_trgm индексы не удалены: %r", exc)
