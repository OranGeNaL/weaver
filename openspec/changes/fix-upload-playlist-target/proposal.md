## Why

PWA при загрузке своих файлов предлагает «Добавить ещё и в плейлист» и отправляет `UploadCommitIn.playlist_id` — `uid` выбранного плейлиста Hub. `POST /api/uploads/commit` это поле не читает: треки импортируются в библиотеку, но в плейлист не попадают, а клиент не получает ошибку. Это разрыв клиент-серверного контракта.

## What Changes

- `POST /api/uploads/commit` читает `UploadCommitIn.playlist_id` как `playlist_uid` — `uid` выбранного плейлиста Hub.
- Плейлист ищется среди плейлистов текущего пользователя (`playlists.get_owned`); неизвестный или чужой `playlist_id` даёт `404` с русским `detail` до импорта файлов, поэтому пользовательские файлы не теряются.
- Плейлист проверяется до импорта, а его строка блокируется до конца транзакции: параллельное удаление не может сломать commit после того, как файлы уже импортированы.
- Импорт синхронный: `import_file` возвращает трек, и его `uid` известен сразу. После цикла импорта Hub одним вызовом `playlists.add_tracks` добавляет `uid` успешно импортированных треков в конец плейлиста в порядке `tracks` запроса; `anchor_uid` не задаётся, потому что вставка не фоновая.
- Отложенные добавления и `PendingPlaylistAdd` не используются: они относятся к загрузкам, где `download_item_id` обязателен.
- Без `playlist_id` поведение не меняется.
- Добавляются тесты на порядок добавления, отсутствие `playlist_id`, неизвестный и чужой плейлист.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `library`: добавляется требование о добавлении импортированных upload-треков в выбранный плейлист Hub.

## Impact

- `hub/app/api/uploads.py` — чтение `playlist_id`, проверка владельца и один вызов `playlists.add_tracks` после импорта.
- `hub/tests/test_uploads.py` — новые тесты.
- `hub/API.md` — уточнение контракта `UploadCommitIn.playlist_id`.
- Схема БД, Alembic-миграции, фоновые задачи, PWA и формат ответа `UploadCommitOut` не меняются; обратная совместимость сохраняется (без поля — прежнее поведение).
- Проверка: `openspec validate fix-upload-playlist-target`, `ruff check`, `ruff format --check`, `pyright`, `pytest -k "upload or playlist"` и полный `pytest`.
