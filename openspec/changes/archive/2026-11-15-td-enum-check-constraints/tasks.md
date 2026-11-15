## 1. Инвентаризация

- [x] 1.1 Зафиксировать первый набор колонок и источники значений: `imports.source` ← `ImportSource`, `imports.method` ← `ImportMethod`, `imports.mode` ← `ImportMode`, `import_collections.kind` ← `CollectionKind`, `albums.release_type` ← `ReleaseType` (nullable), `mix_settings.mode`/`mixes.mode`/`wave_sessions.mode` ← `MixMode`.

  Поправка по факту кода: колонки `import_items.kind` в схеме нет — `ImportItemOut.kind` вычисляется в
  `app.imports.core.item_out` из `ImportCollection.kind` через `ITEM_KIND`, поэтому `ItemKind` ограничивается
  колонкой `import_collections.kind` (она в наборе), а отдельного ограничения для `import_items` не нужно.
  Новую колонку не вводим: change — только про CHECK-ограничения (non-goal «не менять типы/состав колонок»).

- [x] 1.2 Утвердить имена ограничений `ck_<таблица>_<колонка>`; статус-поля (`jobs.status`, `download_items.status`, `imports.status`, `import_items.status`, `assistant_tasks.status`, `uploads.status`) вынести за скобки этого change'а.

  Имена: `ck_imports_source`, `ck_imports_method`, `ck_imports_mode`, `ck_import_collections_kind`,
  `ck_albums_release_type`, `ck_mix_settings_mode`, `ck_mixes_mode`, `ck_wave_sessions_mode`. Статус-поля не
  тронуты (state machines, см. design.md Non-Goals). Другие касты "БД → Literal" (`favorite_subjects.target_type`)
  в первый набор не входят — отдельный `td-*` change (найдено советом валидации).

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

- [x] 4.1 `pytest -q -n 4` на тестовом Postgres: зелёный (база: 1081 passed).

  `TEST_DATABASE_URL=postgresql+asyncpg://hub:hub@localhost:5433/hub_test_enum uv run pytest -q -n 4`:
  **1161 passed, 74 skipped, 0 failed** за 622.51s. Число «1081» в постановке устарело: на ветке до change'а
  проходят 1129 тестов, 32 из 1161 — новые (`tests/test_enum_constraints.py`).

- [x] 4.2 `pyright` по `hub/pyrightconfig.json`: 0 ошибок — `uv run pyright`: `0 errors, 0 warnings, 0 informations`.
- [x] 4.3 `ruff check` и `ruff format --check`: без ошибок — `All checks passed!`, `196 files already formatted`.
- [x] 4.4 Проверить, что валидные пути записи (создание импорта, коллекций, элементов, альбома, микса) не изменились.

  Пути записи не трогались (менялись только `__table_args__` и миграция), и они покрыты зелёным полным прогоном:
  `tests/test_imports.py` (создание импорта, коллекций, элементов, economy/full), `tests/test_mixes.py`
  (микс дня, настройки), `tests/test_library_catalog.py` (альбом и его `release_type`), `tests/test_wave.py`
  (`wave_sessions.mode`).

## Меморандум council-validate

**Вопрос и scope:** корректно ли реализован `td-enum-check-constraints` (CHECK-ограничения 8 колонок первого набора, миграция, тесты, delta-спека `persistence`) до архивации.

**Вердикт: PASS.** Блокеров и P1 нет; два круга правок текста закрыты, код и миграция не менялись после первого прохода.

**Консенсус:** 8 ограничений `ck_<таблица>_<колонка>` объявлены и в моделях, и в миграции `j0f7e3c5d9a4`, имена и выражения совпадают; `upgrade head` ↔ `downgrade -1` обратимы; тесты доказывают не только наличие констрейнта в `pg_constraint`, но и принуждение (`IntegrityError` на INSERT/UPDATE, строка не сохраняется, NULL у `albums.release_type`), плюс паритет с `get_args(Literal)`; валидные пути записи не сломаны (полный прогон).

**P1 (исправлен):** delta-спека обещала ограничить все колонки, на которые опираются касты «БД → Literal», а реализованы 8 первого набора; `favorite_subjects.target_type` (`app/favorites.py`) остаётся на соглашении. Cross-exam (2/2 accept) решил сузить формулировку, а не добавлять 9-й констрейнт. Требование теперь перечисляет 8 колонок, прочие касты вынесены в Non-Goals design, Impact proposal, примечание tasks 1.2 как отдельный `td-*` change.

**P1 второго круга (исправлен, мой):** в правке был указан несуществующий `favorite_events.target_type`; упоминание удалено, остался реальный `favorite_subjects.target_type`. Заодно убраны из первого набора `import_items.kind` (design §2, proposal Why, tasks 1.1, README) — `ItemKind` выводится из `import_collections.kind` через `ITEM_KIND`.

**Отклонено/понижено:** «`alembic autogenerate` защищает от рассинхронизации модели и миграции» — `alembic check` в репозитории красный по pre-existing причинам и CHECK не сравнивает; это P2 (документационный риск), БД остаётся источником принуждения.

**Решения владельца:** P2 оставлены без правок до архивации: (1) CHECK добавляется как VALID и упадёт на непустой БД с невалидными строками — приемлемо, рабочих инстансов нет, схема поднимается с нуля; (2) отдельный `td-*` change на паритет `models.__table_args__` ↔ миграция и на `favorite_subjects.target_type`; (3) статус-поля (state machines) — вне scope.

**Доказательства:** `pytest -q -n 4` → 1163 passed, 74 skipped (мастер; ветка change 1161 = 1129 база + 32 новых); `pyright` → 0; `ruff check`/`format --check` → чисто; `alembic upgrade head` → `j0f7e3c5d9a4`, `downgrade -1` → `i9e6f2d4b8c3`, `upgrade head` → head; `openspec validate --strict` → valid. Run ids: pass 1 — `04aa1086`; cross-exam — `9d6d646d`; re-check — `ee0c1dc2`; verify — `ffbcc994`.

**Уверенность:** высокая (модель ↔ миграция ↔ тесты сверены, прогон зелёный). Изменило бы решение: появление рабочей БД с невалидными строками или требование покрыть `favorite_subjects.target_type` в этом же change.

**Ростер:** oracle (context: fork) + reviewer (обычный контекст); 1 проход + cross-exam + 2 re-check; фолбэков не было.
