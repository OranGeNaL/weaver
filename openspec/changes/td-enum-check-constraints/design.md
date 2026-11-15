## Context

См. `proposal.md — Why`. `td-typing-debt` закрыл ошибки типов, но оставил касты `БД → Literal`
(`cast(ImportSource, imp.source)` и подобные) без runtime-гарантии: колонки — `VARCHAR`, набор значений
описан комментариями и Pydantic-схемами. Валидные пути записи пишут только литералы; ограничение в БД
зафиксирует это правило и уберёт расхождение.

## Goals / Non-Goals

**Goals:**
- Инвариант «значение ∈ Literal» обеспечивается Postgres, а не соглашением.
- Наборы в БД и в `app/api/schemas.py` совпадают и проверяются тестом.
- Миграция с рабочим откатом; схема самодокументируемая (`CheckConstraint` в моделях).

**Non-Goals:**
- Не переводить статус-поля (`jobs.status`, `download_items.status`, `imports.status`, `assistant_tasks.status`,
  `uploads.status`) — это state machines, наборы меняются чаще; отдельный шаг.
- Не вводить нативные PG-enum типы и не менять типы колонок.
- Не ограничивать касты "БД → Literal" вне первого набора: `favorite_subjects.target_type` и
  `favorite_events.target_type` (`app/favorites.py`) остаются на соглашении — отдельный `td-*` change.
- Не добавлять валидацию в API поверх существующих Pydantic-схем.
- Не менять поведение валидных путей записи.

## Decisions

### 1. CHECK-ограничения, а не `Enum(native_enum=False)`

`sa.CheckConstraint("source IN ('yandex','spotify')", name="ck_imports_source")` не меняет тип колонки,
прост в миграции и откате и переносим между диалектами (полезно тестам). `Enum(native_enum=False,
create_constraint=True)` дал бы то же, но тянет пересоздание типа при изменении набора и хуже читается
в истории миграций. Нативный `CREATE TYPE` отвергнут: изменение набора требует `ALTER TYPE`, а сравнивать
с `Literal` сложнее.

### 2. Первый набор — только поля, на которые опираются касты

`imports.source/method/mode`, `import_collections.kind`, `import_items.kind`, `albums.release_type`,
`mix_settings.mode`, `mixes.mode`, `wave_sessions.mode`. Наборы берутся из `app/api/schemas.py`
(`ImportSource`, `ImportMethod`, `ImportMode`, `CollectionKind`, `ItemKind`, `MixMode`) и
`app/library/tagger.py` (`ReleaseType`). Nullable-поля (`albums.release_type`) допускают `NULL`.

### 3. Ограничения дублируются в моделях

Те же `CheckConstraint` объявляются в `__table_args__` моделей: это делает схему видимой для разработчика
и не даёт `alembic autogenerate` предлагать удаление ограничений. Единственный источник значений —
`Literal`-типы; в миграции наборы статичны, паритет проверяет тест (см. 4).

### 4. Паритет с `Literal` — тестом

Тест читает `get_args(...)` нужных Literal и проверяет, что каждое значение принимается БД, а
заведомо чужое — отвергается. Так добавление нового провайдера/режима без миграции не пройдёт молча.

### 5. Поведение и откат

Невалидная запись теперь падает `IntegrityError` на INSERT/UPDATE — объявленное изменение контракта
хранения; валидные пути не меняются. Откат миграции — `drop_constraint` в обратном порядке, данные не
затрагиваются.

## Risks / Trade-offs

- [Набор значений зашит в миграцию] → тест паритета с `Literal`; изменение набора — отдельная миграция.
- [Статус-поля остаются без ограничений] → осознанно (state machines), вынесено в Non-Goals.
- [Ошибки на записи вместо чтения] → это и есть цель; сообщение БД логируется как обычная ошибка запроса.
- [Рассинхронизация модели и миграции] → `CheckConstraint` в `__table_args__` + `alembic check` в задачах.

## Migration Plan

1. Зафиксировать наборы и имена ограничений (`ck_<таблица>_<колонка>`).
2. Добавить `CheckConstraint` в модели.
3. Создать миграцию `add enumeration check constraints` (upgrade — `create_check_constraint`, downgrade — `drop_constraint`).
4. Тесты: валидные/невалидные значения, паритет с `Literal`, `upgrade head`/`downgrade -1`/`upgrade head`.
5. Полный `pytest`, `pyright`, `ruff`.

Откат: `alembic downgrade -1`; ограничения исчезают, данные не меняются.

## Open Questions

Нет.
