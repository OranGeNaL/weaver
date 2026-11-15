"""The enumeration CHECK constraints of the text columns (`td-enum-check-constraints`, persistence spec).

Every test fails when the migration is reverted: a value outside the set is written silently again and
`pg_constraint` holds no `ck_<table>_<column>`.

`ImportItem.kind` has no column of its own: `item_out` derives it from the kind of the item's collection
(`ITEM_KIND` in `app.imports.core`), so `import_collections.kind` is the column that constrains it. Status columns
(`imports.status`, `import_items.status`, ...) are state machines and stay out of scope.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_args
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.api.schemas import CollectionKind, ImportMethod, ImportMode, ImportSource, MixMode
from app.imports.sources.base import CollectionKind as SourcesCollectionKind
from app.library.tagger import ReleaseType
from app.models import Album, Import, ImportCollection, Mix, MixSettings, User, WaveSession

# a value of no set below: the tests that write it expect Postgres to refuse the row
_FOREIGN = "other"


@dataclass(frozen=True)
class _Parents:
    """The rows a constrained table points at, created once per test."""

    user_id: int
    import_id: int
    collection_id: int


@dataclass(frozen=True)
class EnumColumn:
    """A constrained column: the Literal it mirrors and how a row holding a value is built."""

    model: Any
    table: str
    column: str
    literal: Any
    make: Callable[[_Parents, str | None], Any]
    nullable: bool = False
    # other Literals over the same set: the write side must not drift from the constraint either
    also: tuple[Any, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.table}.{self.column}"

    @property
    def constraint(self) -> str:
        return f"ck_{self.table}_{self.column}"

    @property
    def values(self) -> tuple[str, ...]:
        return get_args(self.literal)


_CASES: tuple[EnumColumn, ...] = (
    EnumColumn(
        Import,
        "imports",
        "source",
        ImportSource,
        lambda p, v: Import(user_id=p.user_id, source=v, method="device"),
    ),
    EnumColumn(
        Import,
        "imports",
        "method",
        ImportMethod,
        lambda p, v: Import(user_id=p.user_id, source="yandex", method=v),
    ),
    EnumColumn(
        Import,
        "imports",
        "mode",
        ImportMode,
        lambda p, v: Import(user_id=p.user_id, source="yandex", method="device", mode=v),
    ),
    EnumColumn(
        ImportCollection,
        "import_collections",
        "kind",
        CollectionKind,
        lambda p, v: ImportCollection(import_id=p.import_id, kind=v, external_id="likes", title="Мне нравится"),
        also=(SourcesCollectionKind,),
    ),
    EnumColumn(
        Album,
        "albums",
        "release_type",
        ReleaseType,
        lambda p, v: Album(uid=str(uuid4()), title="Альбом", album_artist="Артист", release_type=v),
        nullable=True,
    ),
    EnumColumn(MixSettings, "mix_settings", "mode", MixMode, lambda p, v: MixSettings(user_id=p.user_id, mode=v)),
    EnumColumn(Mix, "mixes", "mode", MixMode, lambda p, v: Mix(user_id=p.user_id, title="Микс дня", mode=v)),
    EnumColumn(
        WaveSession,
        "wave_sessions",
        "mode",
        MixMode,
        lambda p, v: WaveSession(id=uuid4().hex, user_id=p.user_id, mode=v),
    ),
)

_IDS = [case.name for case in _CASES]


async def _parents(s: AsyncSession) -> _Parents:
    """A user, an import and one of its collections: the rows the constrained tables point at."""
    user = User(username=f"enum-{uuid4().hex[:12]}")
    s.add(user)
    await s.flush()
    imp = Import(user_id=user.id, source="yandex", method="device")
    s.add(imp)
    await s.flush()
    coll = ImportCollection(import_id=imp.id, kind="likes", external_id="likes", title="Мне нравится")
    s.add(coll)
    await s.flush()
    return _Parents(user_id=user.id, import_id=imp.id, collection_id=coll.id)


async def _committed_parents() -> _Parents:
    async with db.session_scope() as s:
        return await _parents(s)


async def _count(s: AsyncSession, model: Any) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.mark.parametrize("case", _CASES, ids=_IDS)
async def test_every_literal_value_is_written(db_url, case):
    """Every value of the Literal is accepted on INSERT and on UPDATE; a nullable column takes NULL."""
    db.init_engine(db_url)
    try:
        async with db.session_scope() as s:
            parents = await _parents(s)
            row = case.make(parents, case.values[0])  # the INSERT path
            s.add(row)
            await s.flush()
            for value in case.values[1:]:  # the UPDATE path
                setattr(row, case.column, value)
                await s.flush()
            if case.nullable:
                setattr(row, case.column, None)
                await s.flush()
    finally:
        await db.dispose()


@pytest.mark.parametrize("case", _CASES, ids=_IDS)
async def test_a_foreign_value_is_refused_on_insert(db_url, case):
    """A value outside the set fails the CHECK and the row is not stored."""
    assert _FOREIGN not in case.values
    db.init_engine(db_url)
    try:
        parents = await _committed_parents()
        async with db.session_scope() as s:
            before = await _count(s, case.model)
            s.add(case.make(parents, _FOREIGN))
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()
        async with db.session_scope() as s:
            assert await _count(s, case.model) == before
    finally:
        await db.dispose()


@pytest.mark.parametrize("case", _CASES, ids=_IDS)
async def test_a_foreign_value_is_refused_on_update(db_url, case):
    """A row that is already stored keeps its value when an UPDATE leaves the set."""
    assert _FOREIGN not in case.values
    db.init_engine(db_url)
    try:
        parents = await _committed_parents()
        async with db.session_scope() as s:
            row = case.make(parents, case.values[0])
            s.add(row)
            await s.flush()
            key = inspect(row).identity
        async with db.session_scope() as s:
            stored = await s.get(case.model, key)
            assert stored is not None
            setattr(stored, case.column, _FOREIGN)
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()
        async with db.session_scope() as s:
            kept = await s.get(case.model, key)
            assert kept is not None
            assert getattr(kept, case.column) == case.values[0]
    finally:
        await db.dispose()


@pytest.mark.parametrize("case", _CASES, ids=_IDS)
async def test_the_db_set_matches_the_literal(db_url, case):
    """The constraint of the column holds exactly the values of its Literal: a new one needs a migration."""
    db.init_engine(db_url)
    try:
        async with db.session_scope() as s:
            rows = (
                await s.execute(
                    select(text("conname"), text("pg_get_constraintdef(oid)"))
                    .select_from(text("pg_constraint"))
                    .where(text("contype = 'c'"))
                )
            ).all()
        definitions = {name: definition for name, definition in rows}
        assert case.constraint in definitions, f"{case.constraint} is missing: no such enumeration constraint"
        in_db = frozenset(re.findall(r"'([^']*)'::", definitions[case.constraint]))
        assert in_db == set(case.values)
        for literal in (case.literal, *case.also):
            assert set(get_args(literal)) == in_db
    finally:
        await db.dispose()
