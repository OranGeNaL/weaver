## 1. Код

- [x] 1.1 В `hub/app/api/uploads.py::commit` читать `UploadCommitIn.playlist_id` как `uid` плейлиста Hub.
- [x] 1.2 Проверять владельца через `playlists.get_owned` до импорта; неизвестный или чужой плейлист → `404` с русским `detail`.
- [x] 1.3 Блокировать строку плейлиста до конца транзакции (`playlists.lock`).
- [x] 1.4 Собирать `uid` успешно импортированных треков в порядке `tracks` и добавлять их одним вызовом `playlists.add_tracks` после импорта.
- [x] 1.5 Не создавать отложенные добавления и не менять схему БД.

## 2. Тесты

- [x] 2.1 `hub/tests/test_uploads.py`: commit с `playlist_id` кладёт треки в плейлист в порядке `tracks`.
- [x] 2.2 Тест: без `playlist_id` плейлисты не меняются.
- [x] 2.3 Тест: неизвестный `playlist_id` даёт `404`, файлы остаются в staging.
- [x] 2.4 Тест: чужой плейлист даёт `404`.
- [x] 2.5 Тест: потерянный staging-файл не добавляется, остальные треки попадают в плейлист в порядке запроса.

## 3. Документация и проверка

- [x] 3.1 Обновить `hub/API.md` для `UploadCommitIn.playlist_id`.
- [x] 3.2 `openspec validate fix-upload-playlist-target`.
- [x] 3.3 `cd hub && uv run ruff check && uv run ruff format --check`.
- [x] 3.4 `cd hub && uv run pyright`.
- [x] 3.5 `cd hub && TEST_DATABASE_URL=... uv run pytest -q tests/ -k "upload or playlist"`.
- [x] 3.6 Полный `pytest -q -n 8` не хуже эталона (1124 passed / 74 skipped до правок).
