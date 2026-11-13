## Why

Зависит от `navidrome-migration-0-governance` (правила `config.yaml`).

После отказа от Navidrome Hub становится единственным владельцем байтов библиотеки. Файловая система перестаёт быть достаточной: нужны версионирование, дедупликация, отвязка от конкретного диска и единый контракт хранения. Выбрана модель S3 primary с локальным hot-cache и иммутабельными блобами, а метаданные живут в БД.

## What Changes

- **BREAKING** Библиотека хранится в S3 (RustFS в compose по умолчанию, внешний S3 через конфиг); локальный диск — staging и hot-cache.
- Ввести `storage.backend` (fs | s3) и сделать S3 основным бэкендом; fs — только dev/test, не production-режим.
- Ввести иммутабельные content-addressed блобы (`sha256`): один блоб может обслуживать несколько треков; метаданные трека — в БД.
- Ввести двухфазную запись объекта (`pending` → `committed`) с ретраями и GC осиротевших блобов.
- Ввести локальный hot-cache для стриминга, ffprobe/ffmpeg и embedder с LRU и лимитом размера.
- Писать S3 user-metadata и manifest при импорте как страховку восстановления.
- Заменить `os.replace`/`shutil.move` и файловые `trash`/`.backups` на объектные операции, префикс `trash/` и lifecycle/GC.
- Перевести embedder на presigned URL вместо локального пути.
- Заменить отчёт о свободном месте на отчёт о ёмкости бакета и кэша.
- Оставить staging локальным.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `storage`: physical layout, atomic move, unique names/versions, trash, probe, listen copies and free-space reporting заменяются объектной моделью S3, блобами, двухфазной записью, кэшем и GC.
- `system`: health должен сообщать состояние S3 (доступность бакета, кэш), чтобы клиенты и администратор видели деградацию.

## Impact

- Код: `hub/app/library/layout.py`, `importer.py`, `tagger.py`, `editor.py`, `probe.py`, `owned.py`; `hub/app/listen.py`, `hub/app/light.py`, `hub/app/api/uploads.py`, `hub/app/api/library.py`, `hub/app/jobs/downloads.py`, `hub/app/housekeeping.py`, `hub/app/config.py`, `hub/app/mixes/embedder.py`, `hub/app/mixes/build.py`.
- БД: таблица `blobs` (sha256, size, content_type, status, created_at, verified_at), ссылки из `tracks`/`albums` на блоб; миграция обязательна.
- API: ответы библиотеки не меняются; меняется источник байтов и идентичность обложек; добавляется health-статус S3. `free_bytes` в статистике импорта MAY сменить смысл на свободное место локального кэша/staging; это объявляется в спеке `storage`, а не молча.
- Инфраструктура: `docker-compose.yml` (RustFS), `.env`/`.env.example` (S3-креды, кэш), README/ARCHITECTURE.
- Клиенты: PWA не меняется; embedder получает URL вместо path.
- Проверка: `pytest` (включая S3-фейк и MinIO/RustFS smoke), `ruff`, `alembic`, smoke на RustFS.
- Обратная совместимость: локальная раскладка `MUSIC_ROOT/library` больше не является источником истины; объявлено ломающим, рабочих инстансов нет.
