## 1. Инфраструктура и конфигурация

- [x] 1.1 Добавить сервис `postgres` в `docker-compose.yml` (образ `postgres:17`, volume, healthcheck, креды из `.env`) и `depends_on: condition: service_healthy` у Hub; проверить `docker compose config` и успешный старт.
- [x] 1.2 Добавить `DATABASE_URL` в `.env.example` и `hub/app/config.py`, поддержать внешний Postgres; проверить, что без `DATABASE_URL` приложение падает с понятной ошибкой, а не создаёт SQLite-файл.
- [x] 1.3 Заменить `sqlalchemy.url` в `hub/alembic.ini` на плейсхолдер Postgres, обновить `run_migrations`, `migrations/env.py` (sync-URL из `DATABASE_URL`) и убрать `render_as_batch`; проверить `alembic current` на пустой базе.
- [x] 1.4 Добавить `asyncpg` и `psycopg[binary]` в `hub/pyproject.toml`, выполнить `uv lock`, удалить `aiosqlite`; установить `postgresql-client-17` из PGDG, `POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C.UTF-8`; проверить `pg_dump --version`, совместимость с сервером и `lower('Кино')='кино'`.

## 2. Движок и миграции

- [x] 2.1 Переписать `hub/app/db.py`: `postgresql+asyncpg`, убрать PRAGMA/WAL, добавить ограниченный retry подключения; проверить тест на недоступную БД и успешный старт после появления Postgres.
- [x] 2.2 Удалить 16 SQLite-миграций из активного каталога и создать одну initial-миграцию целевой схемы; проверить `alembic upgrade head`, повторный `upgrade head` (no-op) и `downgrade base`.
- [x] 2.3 Перевести `JSON`-колонки в `JSONB` (включая `Base.type_annotation_map`) и добавить btree-индексы по `(col->>'path')::int` для фильтров `->>`, GIN — только для `@>`; проверить `alembic upgrade head` и тест на выборку по JSONB-пути.

## 3. Диалектная нейтральность

- [x] 3.1 Заменить `func.json_extract(...)` на `->>` по умолчанию (и `?`/`->` только где домен различает null/missing) с явными кастами чисел; покрыть сайты `plays.py:58,77,111`, `api/downloads.py:76`, `mixes/core.py:77,87,194,308,320`, `mixes/wave.py:752`, `api/mixes.py:69`, `imports/apply.py:172,197`, `imports/core.py:107,176`; проверить `rg "json_extract"` (пусто) и тесты обоих null-случаев.
- [x] 3.2 Заменить `sqlalchemy.dialects.sqlite.insert` на `dialects.postgresql.insert` (`catalog/cache.py`, `imports/resolve.py`, `ai/limits.py`, `favorites.py`, `plays.py`, `api/search.py`, `mixes/wave.py`, `mixes/smart.py`); проверить `rg "dialects.sqlite"` (пусто) и тесты upsert/идемпотентности.
- [x] 3.3 Переписать `hub/app/mixes/evaluate.py` с прямого `sqlite3` на SQLAlchemy/Postgres: `--url` и read-only роль/транзакция вместо `PRAGMA query_only`; проверить запуск evaluate на тестовой БД.
- [x] 3.4 Переписать claim очереди в `jobs/queue.py` на `FOR UPDATE SKIP LOCKED` (или эквивалент) с повторением предиката; проверить тест параллельного захвата одной задачи двумя воркерами.

## 4. Поиск и временные метки

- [x] 4.1 Перевести поиск на `lower`/`ILIKE` и `search_text` (`normalize_title(artist+title+album)`), создать `pg_trgm` нефатальной миграцией через `context.autocommit_block()`/SAVEPOINT; `tracks.search_text`/`albums.search_text` — в initial-миграции и пересчёт при правке тегов; переписать альбомный поиск `api/library.py:78-85` на `albums.search_text ILIKE ALL`; проверить `lower('Кино')='кино'`, `q='ёлка!'` → `Ёлка`, многотокенный поиск и поведение без расширения.
- [x] 4.2 Оставить `utcnow()` как функцию, но вернуть aware `datetime.now(UTC)`; проверить сравнения/bind в `auth.py`, `accounts.py`, `catalog/cache.py`, `jobs/queue.py`, `mixes/build.py`, клиентские timestamps в `mixes/wave.py`/`mixes/smart.py`, `parse_time` и monkeypatch-тесты; проверить тесты сериализации API и логина.
- [x] 4.2b Найти `rg 'replace\(tzinfo=None\)|datetime\.now\('` и перевести/аннотировать `plays.py`, `favorites.py`, `mixes/taste_snapshot.py`, `listening_summary.py`, `insights/analytics.py`, `api/admin.py`; добавить regression-тест naive-vs-aware.
- [x] 4.3 Проверить контракт API на `null`-семантику JSON, формат `+00:00` и отсутствие Python-fallback в поиске; ожидается отсутствие изменений в ответах PWA или объявленное изменение многотокенного поиска.

## 5. Бэкапы

- [x] 5.1 Заменить `sqlite3.backup()` в `hub/app/housekeeping.py` на `pg_dump --format=custom` с ротацией; проверить создание дампа и удаление старых копий.
- [x] 5.2 Добавить проверку восстановления `pg_restore` в отдельную базу (права `CREATEDB`, отдельное имя scratch-БД); проверить совпадение схемы и контрольных строк.
- [x] 5.3 Проверить, что ошибка `pg_dump` логируется и не удаляет предыдущие копии.

## 6. Тесты

- [x] 6.1 Перевести `hub/tests/conftest.py` и прямые `init_engine(db_file)` в 10 тестовых модулях на обязательную `TEST_DATABASE_URL` (без fallback); guard `TEST_DATABASE_URL != DATABASE_URL` — в `app/db.py`; шаблонная БД + клон на воркер + truncate на каждый тест + teardown; проверить `pytest -q` и `pytest -q -n auto`.
- [x] 6.2 Добавить тесты на JSONB-пути, upsert, регистронезависимый поиск и timestamptz; проверить, что каждый новый тест падает при откате соответствующего изменения.
- [x] 6.3 Проверить, что тесты не обращаются к рабочей БД и не требуют SQLite-файлов.
- [x] 6.4 Удалить/переписать sqlite3-зависимые тесты (`test_migration_favorites.py`, `test_migration_insights.py`, `test_plays.py`, `test_accounts.py`, `test_taste_evaluate.py`) и их PRAGMA/`OperationalError`-ожидания.

## 7. Проверка

- [x] 7.1 Прогнать `pytest` на Postgres; ожидается зелёный прогон.
- [ ] 7.2 Прогнать `ruff check` и `ruff format --check`; ожидается без ошибок.
- [x] 7.3 Проверить `alembic upgrade head` и `alembic downgrade -1` на пустой БД.
- [x] 7.4 Проверить запуск Hub с внешним `DATABASE_URL` (без compose-сервиса Postgres).
- [x] 7.5 Прогнать `npm run typecheck` и `npm run build` (правило tasks);
- [x] 7.6 Обновить README/ARCHITECTURE в части Postgres; проверить отсутствие упоминаний SQLite как рабочей БД.
