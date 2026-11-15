## Context

См. `proposal.md` — Why. Состояние, важное для решений:

- `FavoriteSubject` (`app/models.py:614-618`): `target_type: Mapped[str] = mapped_column(String(16))`,
  `__table_args__` содержит только `UniqueConstraint`; значение читается кастом
  `cast(TargetType, state.target_type)` (`app/favorites.py:387`), `TargetType = Literal["track","album","artist"]`
  (`app/favorites.py:35`). Запись идёт из Pydantic-схем, где тип уже `TargetType`.
- Миграция `j0f7e3c5d9a4` (архив `td-enum-check-constraints`) создаёт 8 констрейнтов через
  `create_check_constraint` в `upgrade` и `drop_constraint` в `downgrade`; текущий head — `j0f7e3c5d9a4`.
- `alembic check` красный из-за pre-existing дрейфа (trgm-индексы, inline `ix_download_items_*`) и CHECK
  не сравнивает; `models.metadata` при этом полный и корректный.
- Паритетный тест `hub/tests/test_enum_constraints.py` параметризован кейсами `(таблица, колонка, Literal,
  фабрика строки)` и строит имя `ck_<таблица>_<колонка>`; кейсы проверяют INSERT/UPDATE/`IntegrityError`/`NULL`.

## Goals / Non-Goals

**Goals:**
- Инвариант «значение ∈ Literal» выполняется для всех кастов БД → Literal, включая `favorite_subjects.target_type`.
- Дрейф между `models.__table_args__` и миграциями обнаруживается тестом, независимо от `alembic check`.
- Минимальная правка: одно ограничение, один параметризованный кейс, один тест паритета.

**Non-Goals:**
- Не чинить pre-existing дрейф `alembic check` (trgm-индексы, inline `ix_download_items_*`) — отдельный `td-*`.
- Не ограничивать статус-поля и state machines (решение зафиксировано в архивном меморандуме).
- Не менять API, Pydantic-схемы и способ записи `favorite_subjects`.
- Не вводить валидацию в API поверх существующих схем.

## Decisions

### 1. Одно новое ограничение в существующем стиле (выбрано)

`CheckConstraint("target_type IN ('track', 'album', 'artist')", name="ck_favorite_subjects_target_type")`
в `FavoriteSubject.__table_args__` + `create_check_constraint`/`drop_constraint` в новой ревизии с
`down_revision = "j0f7e3c5d9a4"`.

Почему: совпадает со стилем уже принятых 8 ограничений; `String(16)` остаётся как есть (native enum
отвергнут в прошлом change'е из-за сложности `ALTER TYPE`); `NULL` не встречается — колонка NOT NULL.

### 2. Тест паритета моделей и миграций читает `models.metadata` (выбрано)

Тест собирает `{имя: выражение}` из `models.metadata` для всех `CheckConstraint` с префиксом `ck_`, читает
`pg_get_constraintdef` из БД после миграций и сравнивает множества имён; выражения сравниваются
нормализованно (снятие внешних скобок и кавычек, приведение регистра и пробелов к одному виду) — точное
текстовое совпадение PostgreSQL и SQLAlchemy недостижимо и хрупко.

Почему: `alembic check` не сравнивает CHECK, а `autogenerate` их теряет; тест на `models.metadata` ловит
дрейф в обе стороны и не зависит от состояния индексов.

Альтернатива (отклонена): сравнивать только имена без выражений — пропускает подмену набора значений при
том же имени, то есть ровно тот регресс, ради которого заводится change.

### 3. Миграция валидирующая (VALID), без `NOT VALID` (выбрано)

Почему: рабочих инстансов нет, схема поднимается с нуля; `NOT VALID` в проекте не практикуется и оставил бы
непроверенные строки. Цена: `upgrade` упадёт на dev-БД с невалидным `target_type` — это объявленный и
желаемый отказ.

## Risks / Trade-offs

- [Нормализация выражений может дать ложное расхождение] → тест сравнивает множества имён отдельно от
  выражений: имя проверяется строго, выражение — нормализованно; при расхождении тест печатает оба текста.
- [Новое ограничение ломает существующие тесты, пишущие произвольный `target_type`] → поиск по `hub/tests`
  по `target_type`; все записи должны использовать `TargetType`.
- [Pydantic-схемы допускают `target_type`, отсутствующий в Literal] → `TargetType` — Literal в
  `app/favorites.py`, используется в `FavoriteTarget`; паритетный тест ловит расхождение при изменении набора.
- [Тест паритета требует мигрированной БД в фикстуре] → использует существующую фикстуру `pg_url`/
  `template_name`; дополнительных баз не создаёт.

## Migration Plan

1. Добавить `CheckConstraint` в модель.
2. `alembic revision -m "favorite subjects target type check"`, `down_revision = "j0f7e3c5d9a4"`;
   `upgrade` — `create_check_constraint`, `downgrade` — `drop_constraint`.
3. Прогнать `alembic upgrade head`, `downgrade -1`, `upgrade head` на чистой БД.
4. Откат: `downgrade -1` удаляет только `ck_favorite_subjects_target_type`; остальные 8 ограничений не
   затрагиваются. Код без миграции не работает (модель объявляет констрейнт) — откатывать вместе.

## Open Questions

Нет.
