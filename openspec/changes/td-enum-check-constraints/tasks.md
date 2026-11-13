## 1. Инвентаризация

- [ ] 1.1 Зафиксировать первый набор колонок и источники значений: `imports.source` ← `ImportSource`, `imports.method` ← `ImportMethod`, `imports.mode` ← `ImportMode`, `import_collections.kind` ← `CollectionKind`, `import_items.kind` ← `ItemKind`, `albums.release_type` ← `ReleaseType` (nullable), `mix_settings.mode`/`mixes.mode`/`wave_sessions.mode` ← `MixMode`.
- [ ] 1.2 Утвердить имена ограничений `ck_<таблица>_<колонка>`; статус-поля (`jobs.status`, `download_items.status`, `imports.status`, `import_items.status`, `assistant_tasks.status`, `uploads.status`) вынести за скобки этого change'а.

## 2. Схема и модели

- [ ] 2.1 Добавить `CheckConstraint(..., name="ck_...")` в `__table_args__` затронутых моделей `hub/app/models.py`.
- [ ] 2.2 Создать Alembic-миграцию `add enumeration check constraints`: `create_check_constraint` в `upgrade`, `drop_constraint` в `downgrade`.
- [ ] 2.3 Проверить `alembic upgrade head`, `alembic check` (нет расхождений модели и БД), `alembic downgrade -1`, снова `upgrade head` на пустой базе.

## 3. Тесты

- [ ] 3.1 Тест: каждое значение из Literal-наборов принимается БД (INSERT/UPDATE).
- [ ] 3.2 Тест: значение вне набора отклоняется (`IntegrityError`), строка не сохраняется; `NULL` допустим там, где поле nullable.
- [ ] 3.3 Тест паритета: `get_args(Literal)` ↔ ограничение БД (добавление значения без миграции не проходит молча).

## 4. Проверка

- [ ] 4.1 `pytest -q -n 4` на тестовом Postgres: зелёный (база: 1081 passed).
- [ ] 4.2 `pyright` по `hub/pyrightconfig.json`: 0 ошибок.
- [ ] 4.3 `ruff check` и `ruff format --check`: без ошибок.
- [ ] 4.4 Проверить, что валидные пути записи (создание импорта, коллекций, элементов, альбома, микса) не изменились.
