"""enumeration check constraints: the text columns the DB -> Literal casts rely on

Revision ID: j0f7e3c5d9a4
Revises: i9e6f2d4b8c3
Create Date: 2027-01-10 12:00:00.000000

`td-enum-check-constraints`: the values of the enumerated text columns were held by convention and by the `Literal`
types of `app/api/schemas.py` alone, so `cast(ImportSource, imp.source)` promised nothing. Postgres now rejects a
value outside the set:

- `imports.source` (ImportSource), `imports.method` (ImportMethod), `imports.mode` (ImportMode);
- `import_collections.kind` (CollectionKind);
- `albums.release_type` (ReleaseType; nullable, NULL passes a CHECK);
- `mix_settings.mode`, `mixes.mode`, `wave_sessions.mode` (MixMode).

`ImportItem.kind` has no column: it is derived from the kind of its collection (`ITEM_KIND`), so
`import_collections.kind` is the column that constrains it. Status columns are state machines and stay out of scope.

Downgrade: `drop_constraint`; rows are not touched.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "j0f7e3c5d9a4"
down_revision: str | None = "i9e6f2d4b8c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (constraint name, table, condition): the same names and conditions as in `app.models`
_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("ck_imports_source", "imports", "source IN ('yandex','spotify')"),
    ("ck_imports_method", "imports", "method IN ('device','token','profile','file')"),
    ("ck_imports_mode", "imports", "mode IN ('economy','full')"),
    ("ck_import_collections_kind", "import_collections", "kind IN ('likes','playlist','albums','artists','dislikes')"),
    ("ck_albums_release_type", "albums", "release_type IN ('album','single','ep','compilation')"),
    ("ck_mix_settings_mode", "mix_settings", "mode IN ('normal','favorites','discover')"),
    ("ck_mixes_mode", "mixes", "mode IN ('normal','favorites','discover')"),
    ("ck_wave_sessions_mode", "wave_sessions", "mode IN ('normal','favorites','discover')"),
)


def upgrade() -> None:
    for name, table, condition in _CHECKS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for name, table, _condition in reversed(_CHECKS):
        op.drop_constraint(name, table, type_="check")
