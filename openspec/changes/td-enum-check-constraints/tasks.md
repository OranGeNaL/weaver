## 1. Инвентаризация

- [x] 1.1 Зафиксировать первый набор колонок и источники значений: `imports.source` ← `ImportSource`, `imports.method` ← `ImportMethod`, `imports.mode` ← `ImportMode`, `import_collections.kind` ← `CollectionKind`, `import_items.kind` ← `ItemKind`, `albums.release_type` ← `ReleaseType` (nullable), `mix_settings.mode`/`mixes.mode`/`wave_sessions.mode` ← `MixMode`.

  Поправка по факту кода: колонки `import_items.kind` в схеме нет — `ImportItemOut.kind` вычисляется в
  `app.imports.core.item_out` из `ImportCollection.kind` через `ITEM_KIND`, поэтому `ItemKind` ограничивается
  колонкой `import_collections.kind` (она в наборе), а отдельного ограничения для `import_items` не нужно.
  Новую колонку не вводим: change — только про CHECK-ограничения (non-goal «не менять типы/состав колонок»).

- [x] 1.2 Утвердить имена ограничений `ck_<таблица>_<колонка>`; статус-поля (`jobs.status`, `download_items.status`, `imports.status`, `import_items.status`, `assistant_tasks.status`, `uploads.status`) вынести за скобки этого change'а.

  Имена: `ck_imports_source`, `ck_imports_method`, `ck_imports_mode`, `ck_import_collections_kind`,
  `ck_albums_release_type`, `ck_mix_settings_mode`, `ck_mixes_mode`, `ck_wave_sessions_mode`. Статус-поля не
  тронуты (state machines, см. design.md Non-Goals).

## 2. Схема и модели

- [x] 2.1 Добавить `CheckConstraint(..., name="ck_...")` в `__table_args__` затронутых моделей `hub/app/models.py`.
- [x] 2.2 Создать Alembic-миграцию `add enumeration check constraints`: `create_check_constraint` в `upgrade`, `drop_constraint` в `downgrade` (`hub/migrations/versions/j0f7e3c5d9a4_enum_check_constraints.py`).
- [x] 2.3 Проверить `alembic upgrade head`, `alembic check` (нет расхождений модели и БД), `alembic downgrade -1`, снова `upgrade head` на пустой базе.

  `upgrade head` → `downgrade -1` (остаются только `ck_artists_name_norm_not_empty`) → `upgrade head` проходят.
  `alembic check` красный и на базе (master) тоже: до change'а он показывает те же расхождения — индексы
  `ix_tracks_search_text_trgm`/`ix_albums_search_text_trgm` созданы миграцией `b1f7c2d94e30`, но не объявлены в
  моделях, и inline-индексы `ix_download_items_import_album/import_id/mix_id/track_ref` рендерятся иначе.
  Набор расхождений до и после change'а совпадает дословно (`diff` по строкам `Detected ...`), то есть моя
  миграция ничего не добавила; чинить pre-existing расхождения — вне scope этого change'а.

## 3. Тесты

- [x] 3.1 Тест: каждое значение из Literal-наборов принимается БД (INSERT/UPDATE) — `test_every_literal_value_is_written` (INSERT первого значения, UPDATE по остальным, NULL для nullable).
- [x] 3.2 Тест: значение вне набора отклоняется (`IntegrityError`), строка не сохраняется; `NULL` допустим там, где поле nullable — `test_a_foreign_value_is_refused_on_insert` и `test_a_foreign_value_is_refused_on_update` (строка сохраняет прежнее значение).
- [x] 3.3 Тест паритета: `get_args(Literal)` ↔ ограничение БД (добавление значения без миграции не проходит молча) — `test_the_db_set_matches_the_literal` читает `pg_get_constraintdef` и сверяет набор с `Literal` (включая `CollectionKind` из `app.imports.sources.base` — сторону записи).

## 4. Проверка

- [ ] 4.1 `pytest -q -n 4` на тестовом Postgres: зелёный (база: 1081 passed).
- [ ] 4.2 `pyright` по `hub/pyrightconfig.json`: 0 ошибок.
- [ ] 4.3 `ruff check` и `ruff format --check`: без ошибок.
- [ ] 4.4 Проверить, что валидные пути записи (создание импорта, коллекций, элементов, альбома, микса) не изменились.
