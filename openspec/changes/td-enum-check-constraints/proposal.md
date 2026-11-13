## Why

Ряд перечислимых полей Hub объявлен в БД обычным текстом (`String(16/32)`): `imports.source/method/mode`,
`import_collections.kind`, `import_items.kind`, `albums.release_type`, `mix_settings.mode`, `mixes.mode`,
`wave_sessions.mode`. Допустимые значения сейчас держатся только соглашением и `Literal`-типами
Pydantic на границе API, поэтому `cast(ImportSource, imp.source)` — обещание, а не проверка: невалидное
значение из старой строки или бага попадёт в БД и всплывёт позже `ResponseValidationError` на чтении
(так уже случилось с `InsightsTasteTrackOut.artist`). Инвариант должен быть истинным по построению.

## What Changes

- Добавить CHECK-ограничения Postgres для перечислимых текстовых колонок (первый набор — поля, на которые
  опираются касты `БД → Literal`), с именами `ck_<таблица>_<колонка>`.
- Объявить те же `CheckConstraint` в моделях (`__table_args__`), чтобы схема была самодокументируемой и
  `alembic autogenerate` не терял ограничения.
- Создать Alembic-миграцию `add enumeration check constraints`; откат — `drop_constraint`.
- Добавить тесты: допустимое значение принимается, недопустимое — `IntegrityError`; наборы `Literal`
  в `app/api/schemas.py` совпадают с наборами в БД.
- Не менять API, формат ответов и валидные пути записи.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `persistence`: добавляется требование «Допустимые значения перечислимых полей» — ограничение на уровне Postgres.

## Impact

- БД: новая Alembic-миграция; ограничения на 7–9 колонок.
- Код: `hub/app/models.py` (`CheckConstraint` в `__table_args__`), миграция, тесты.
- Поведение: невалидная запись теперь завершается ошибкой БД — это объявленное изменение контракта хранения.
- API, клиенты, фоновые задачи: без изменений.
- Проверка: `alembic upgrade head` / `downgrade -1` / `upgrade head`, `pytest`, `pyright`, `ruff`.
