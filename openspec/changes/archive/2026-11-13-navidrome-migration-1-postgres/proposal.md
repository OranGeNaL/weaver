## Why

Зависит от `navidrome-migration-0-governance` (правила `config.yaml`).

Hub использует SQLite как единственную БД: это ограничивает параллельную запись, тянет диалект-специфичный SQL (`json_extract`, `sqlalchemy.dialects.sqlite.insert`, поведение `LIKE`) и мешает целевой архитектуре с S3, фоновыми задачами и несколькими клиентами. Рабочих инстансов нет, поэтому переход можно сделать чисто — с пере-базированием схемы, без переноса данных.

## What Changes

- **BREAKING** Заменить SQLite на Postgres: async-драйвер `asyncpg`, подключение через `DATABASE_URL`, без PRAGMA/WAL.
- **BREAKING** Пере-базировать Alembic: вместо 16 SQLite-миграций — одна initial-миграция под Postgres; история SQLite-миграций архивируется.
- Перевести JSON-колонки в `JSONB`; заменить `json_extract` на JSONB-операторы Postgres; заменить `dialects.sqlite.insert` на `dialects.postgresql.insert`.
- Перевести регистронезависимый поиск с SQLite-поведения на Postgres (`lower`/`ILIKE`, при необходимости `pg_trgm`).
- Перевести бэкапы с `sqlite3.backup()` на `pg_dump` с проверкой восстановления.
- Сделать временные метки timezone-aware (`timestamptz`, UTC).
- Перевести тесты Hub на Postgres; убрать шаблонную SQLite-БД из `conftest.py`.
- Добавить Postgres в `docker-compose.yml` (по умолчанию) и поддержать внешний Postgres через `DATABASE_URL`.

## Capabilities

### New Capabilities

- `persistence`: единая СУБД Hub, миграции Alembic, JSONB, временные метки, бэкапы и восстановление, тестовая БД.

### Modified Capabilities

- `system`: требование `Housekeeping` — резервная копия БД выполняется через `pg_dump` и проверяется восстановлением, а не копированием SQLite-файла.

## Impact

- Код: `hub/app/api/library.py` (альбомный поиск), `hub/app/db.py`, `hub/app/models.py`, `hub/app/config.py`, `hub/app/housekeeping.py`, `hub/app/mixes/evaluate.py`, модули с `dialects.sqlite.insert` (`catalog/cache.py`, `imports/resolve.py`, `ai/limits.py`, `favorites.py`, `plays.py`, `api/search.py`, `mixes/wave.py`, `mixes/smart.py`), модули с `json_extract` (`imports/*`, `plays.py`, `mixes/*`, `api/downloads.py`, `api/mixes.py`).
- БД: вся схема; одна initial-миграция; JSON → JSONB; timestamptz; `search_text` для многотокенного поиска.
- API: контракт совместим, но даты получают явный offset (`+00:00`/`Z`); точный формат и `null`-семантика проверяются тестами PWA.
- Инфраструктура: `docker-compose.yml`, `.env` / `.env.example`, `hub/alembic.ini`, health/бэкапы.
- Клиенты: PWA не меняется; Android вне целевой модели.
- Проверка: `pytest`, `ruff`, `npm run typecheck`, `npm run build`, `alembic upgrade head` на пустой БД, `pg_dump`/restore.
- Обратная совместимость: файлы SQLite не поддерживаются; это объявлено ломающим, но рабочих инстансов нет.
