## Context

См. `proposal.md — Why`. Текущий слой: `hub/app/db.py` (SQLite + `aiosqlite`, PRAGMA/WAL, миграции при старте), 16 Alembic-миграций, диалект-специфичные запросы и тестовая шаблонная SQLite-БД в `hub/tests/conftest.py`. Рабочих инстансов нет — данные переносить не нужно.

## Goals / Non-Goals

**Goals:**
- Одна СУБД Postgres для приложения и тестов; детерминированные миграции.
- Убрать диалектную зависимость из доменных запросов.
- Сохранить контракт API и SSE без изменений.

**Non-Goals:**
- Не менять модель очереди и EventBus (in-process).
- Не вводить pgvector и хранение векторов.
- Не масштабировать Hub на несколько процессов.
- Не переносить данные из SQLite.

## Decisions

### 1. Драйвер — `asyncpg`

Выбран `postgresql+asyncpg` через SQLAlchemy async. Альтернатива `psycopg3` проще для sync-инструментов, но `asyncpg` уже ложится в текущую async-модель и быстрее на коротких запросах. В зависимости добавляются `asyncpg` и sync-драйвер `psycopg[binary]` (Alembic CLI), выполняется `uv lock`, `aiosqlite` удаляется. `migrations/env.py` получает sync-URL из `DATABASE_URL` и перестаёт использовать `render_as_batch=True` (он нужен только SQLite). `pg_dump`/`pg_restore` требуют `postgresql-client-17` из PGDG (Debian 12 даёт 15.x, несовместимый с сервером 17); на старте — проверка версии клиента/сервера.

### 2. Пере-базирование миграций

Вместо портирования 16 SQLite-миграций создаётся одна initial-миграция целевой схемы Postgres. Старые файлы удаляются из активного `hub/migrations/versions/`; их история остаётся в git. Альтернатива — портировать каждую миграцию — не нужна без данных и усложняет схему. Rollback initial-миграции — `downgrade base` (удаление схемы) и проверяется на тестовой БД.

### 3. JSONB и индексы

Все JSON-колонки переводятся в `postgresql.JSONB`, включая `Base.type_annotation_map` (`dict[str, Any]` / `list[Any]`), иначе типизация продолжит создавать `json`. Для доступа к значениям по умолчанию используется `->>` (эквивалент `json_extract`), а `->`/`?` — только там, где домен явно различает отсутствующий ключ и JSON-`null`. Числовые id из JSON MUST приводиться явно (`cast(col["k"].astext, Integer)`). Список таких мест: `plays.py:58,77,111`, `api/downloads.py:76`, `mixes/core.py:77,87,194,308,320`, `mixes/wave.py:752`, `api/mixes.py:69`, `imports/apply.py:172,197`, `imports/core.py:107,176`. Для фильтров по `->>` добавляются btree-индексы по выражению `(col->>'path')::int`; GIN — только там, где используется containment `@>`. Upsert — `postgresql.insert(...).on_conflict_do_nothing(...)`.

### 4. Поиск

Регистронезависимость обеспечивается `lower(...)` и `ILIKE`; для нечёткого поиска `pg_trgm` создаётся отдельной **нефатальной** опциональной миграцией: `CREATE EXTENSION` оборачивается в `context.autocommit_block()` (или SAVEPOINT), ошибка логируется и не ломает `upgrade head`. Если расширение недоступно, поиск деградирует до `lower/ILIKE` без падения. Для многотокенного поиска вводятся материализованные колонки `tracks.search_text = normalize_title("{artist} {title} {album}").text` и `albums.search_text = normalize_title("{album_artist} {title}").text`, заполняемые при записи; запрос нормализует `q` тем же `normalize_title(q).text` и сравнивает токены через `search_text ILIKE ALL` (с экранированием `%`/`_`/`\`), сохраняя ё→е/транслитерацию. Альтернатива — объявить изменение поведения с тестом и миграционной заметкой. База MUST создаваться с `--encoding=UTF8` и non-C **LC_CTYPE** (`--locale=C.UTF-8`); на старте — проверка `server_encoding`/`datctype` с русской ошибкой для внешнего Postgres.

### 5. Временные метки

Все `DateTime` переводятся в `DateTime(timezone=True)`; значения трактуются как UTC. `utcnow()` **не удаляется**: он начинает возвращать `datetime.now(UTC)` (aware) и сохраняет импорт/монkeypatch-швы; схема `search_text` добавляется в initial-миграцию и пересчитывается при правке artist/title/album; аудит покрывает `auth.py`, `accounts.py`, `catalog/cache.py`, `jobs/queue.py`, `mixes/build.py` (`.replace(tzinfo=UTC)`), клиентские timestamps (`mixes/wave.py`, `mixes/smart.py`), `parse_time`, а также `.replace(tzinfo=None)` в `plays.py`, `favorites.py`, `mixes/taste_snapshot.py`, `listening_summary.py`, `insights/analytics.py`, `api/admin.py`; monkeypatch-тесты сохраняются. API по-прежнему отдаёт ISO-8601; формат с `+00:00` фиксируется контрактным тестом PWA.

### 6. Тестовая БД

Тесты используют отдельный Postgres. `init_engine()` больше не принимает путь; тесты REQUIRED используют только `TEST_DATABASE_URL` и падают, если она не задана или совпадает с `DATABASE_URL` (guard в `app/db.py`, без fallback на рабочую БД). Один раз создаётся шаблонная база с применёнными миграциями; каждый воркер `pytest-xdist` клонирует её через `CREATE DATABASE ... TEMPLATE` (или отдельную схему), а **каждый тест** получает чистое состояние через truncate. Нужны права `CREATEDB`, отсутствие сессий на template и teardown базы воркера. Прямые вызовы `init_engine(db_file)` в `test_queue_priority`, `test_mix_build`, `test_hitmoz_catalog`, `test_taste_evaluate`, `test_favorites`, `test_insights`, `test_ai_limits`, `test_deezer`, `test_musicbrainz`, `test_mix_sources` переводятся на env. `mixes/evaluate.py` получает `--url` и read-only роль/транзакцию вместо `PRAGMA query_only`.

### 7. Бэкапы

`pg_dump --format=custom` в каталог бэкапов, хранение N последних копий, проверка `pg_restore` в отдельную базу. Альтернатива `pg_basebackup` избыточна для домашнего сервиса.

### 8. Поведение при недоступной БД

Старт Hub повторяет подключение с backoff (ограниченное число попыток), затем завершается с понятной ошибкой. В compose Hub ждёт healthy Postgres. Ошибки запросов возвращают `502`/`500` с русским `detail`, как сейчас; контракт SSE не меняется.

### 9. Очередь задач

`_claim` в `jobs/queue.py` опирается на единственного писателя SQLite: подзапрос выбирает `queued`-строку, а внешний `UPDATE` может захватить её дважды. Под Postgres claim переписывается на `SELECT ... FOR UPDATE SKIP LOCKED` (или `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`) с повторением предиката во внешнем `WHERE` и тестом на параллельный захват.

## Risks / Trade-offs

- [Семантика JSON-путей и `null` отличается от SQLite] → контрактные тесты на каждый `json_extract`-сценарий; проверка `null` и отсутствующего ключа.
- [Смена сериализации дат ломает PWA] → тесты API на форматы и сравнение; capability-флаг не нужен, контракт сохраняется.
- [Тестовая инфраструктура нестабильна] → шаблонная БД, ретраи подключения, явный `TEST_DATABASE_URL`.
- [`pg_trgm` недоступен] → деградация до `lower/ILIKE`, тест на отсутствие расширения.
- [Пере-базирование теряет историю миграций] → история в git, отдельная запись в changelog change.
- [GIN-индексы замедляют запись] → добавлять только на реально фильтруемые поля, замер на тестовых данных.
- [Naive `utcnow()` остаётся в коде] → задача 4.2 требует аудит всех `utcnow()`/сравнений и тест логина/очереди.
- [JSONB-путь даёт text вместо int] → задача 3.1 требует явных кастов и тестов null/missing.
- [Claim очереди дважды] → `FOR UPDATE SKIP LOCKED` + тест.
- [Нет `postgresql-client` в образе] → задача 1.4 добавляет пакет и проверяет `pg_dump` в контейнере.
- [Тесты делят состояние] → truncate/rollback на каждый тест и guard от рабочей БД.

## Migration Plan

1. Добавить Postgres в compose и `DATABASE_URL` в `.env`/`.env.example`.
2. Добавить зависимости (`asyncpg`, `psycopg[binary]`), `postgresql-client` в образ, поправить `migrations/env.py` и `render_as_batch`.
3. Переключить `db.py` на asyncpg, убрать PRAGMA/WAL, настроить retry.
4. Заменить `hub/migrations/versions/*` одной initial-миграцией; проверить `upgrade head` и `downgrade base` на пустой БД.
5. Перевести JSON-колонки, запросы и очередь; заменить sqlite-insert.
6. Перевести поиск, локаль и временные метки.
7. Перевести бэкапы на `pg_dump` + restore-проверку.
8. Перевести тесты на Postgres; убрать SQLite-шаблон.
9. Прогнать `pytest`, `ruff`, проверку API-контрактов PWA.

Откат: вернуть SQLite-код и миграции из git; данные не переносятся, поэтому откат безопасен. Внешний Postgres можно оставить для следующих попыток.

## Open Questions

Нет.
