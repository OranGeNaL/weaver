## 1. Модель каталога

- [ ] 1.1 Добавить таблицу `artists` (`uid`, `name`, `name_norm` UNIQUE, `sort_name`, `cover_blob_id`), `albums.artist_id`, `tracks.integrity_issue` (`missing_blob`) и `albums.integrity_issue` (`missing_cover`); проверить `alembic upgrade head`, откат и отсутствие потери существующих альбомов.
- [ ] 1.4 Реализовать endpoint слияния артистов (`POST /api/library/artists/{uid}/merge`) и тест переноса альбомов/треков.
- [ ] 1.2 Реализовать ленивое создание артиста через `INSERT ... ON CONFLICT (name_norm)`, где `name_norm = normalize_artist(name).latin or .text` + `CHECK (name_norm <> '')`, при импорте и правке; проверить тесты «новый артист» / «существующий артист» / «параллельный импорт» / «слияние» и зафиксировать компромисс с однофамильцами.
- [ ] 1.3 Перевести сериализацию `*Out` на `uid`; для переходного периода отдавать deprecated `id`/`album_id`/`track_id` и `nd_*`, проверить, что новые поля — только `uid`.

## 2. Каталог API

- [ ] 2.1 Реализовать `GET /api/library/home`, `/albums`, `/artists`, `/artists/{uid}`, `/albums/{uid}`, `/tracks/{uid}` с dual lookup `uid`/legacy `int` до этапа 7; проверить контрактные тесты, старые `int`-вызовы PWA и коды `404`.
- [ ] 2.2 Реализовать сортировки `recent`, `frequent`, `newest`, `starred`, `alphabeticalByName`, `alphabeticalByArtist`, `random` с пагинацией (лимит 200), дефолтом `updated_at` до этапа 6 и deprecated `type`→`sort`; проверить тесты порядка и лимитов.
- [ ] 2.3 Реализовать отдачу обложек по `uid` с ресайзом и fallback; проверить тесты `404`, `size` и встроенной обложки.

## 3. Импорт и фиксация

- [ ] 3.1 Заменить `nd_scan` на `catalog_commit` (блоб → трек → `library_track_id`/`uid` → `done`) в одной транзакции с ключом `(download_item_id, sha256)` и колонкой `download_items.blob_sha256` (уникальность — `(download_item_id, blob_sha256)`); сохранить Hub-side effects `nd_scan` через `nd_sync` до этапа 5 (`imports_done`, `mixes_done`); ND-записи инертны без `nd_song_id`/кредов и не валят job; `process_playlist_adds` откладывается до этапа 5; проверить успешную загрузку, повтор и сбой транзакции.
- [ ] 3.2 Добавить административный `POST /api/library/import` для bootstrap-импорта из смонтированного каталога; проверить, что он использует общий `importer` и лимиты.
- [ ] 3.3 Реализовать `catalog_reconcile`: трек без блоба, блоб без ссылок, осиротевшая обложка; reconcile только выставляет/сбрасывает `integrity_issue` и пишет в health/лог, удаляет storage GC; добавить запуск в housekeeping; проверить, что новые объекты в бакете не индексируются.
- [ ] 3.4 Проверить, что импорт, uploads/commit и downloads публикуют `library`-события с прежними именами.

## 4. Переходный период

- [ ] 4.1 Проверить, что `/api/nd/*` продолжает работать без изменений (SHALL до этапа 6); ожидается зелёный прогон существующих тестов medialib.
- [ ] 4.2 Пометить `/api/nd/*` устаревшим в `hub/API.md`; проверить, что новые endpoint'ы описаны в документации.
- [ ] 4.3 Проверить, что `nd_*`-поля остаются deprecated-алиасами и не удалены до этапа 7.
- [ ] 4.4 Проверить dual lookup до этапа 7: старый PWA-вызов `/api/library/albums/{int}` и новый `{uid}` возвращают один альбом.

## 5. Проверка

- [ ] 5.1 Прогнать `pytest` (каталог, импорт, uploads, downloads, reconcile); ожидается зелёный прогон.
- [ ] 5.2 Прогнать `ruff check` и `ruff format --check`; ожидается без ошибок.
- [ ] 5.3 Проверить `alembic upgrade head` и `alembic downgrade -1` для миграции артистов.
- [ ] 5.4 Проверить вручную: импорт трека → появление в `/api/library/albums`, открытие альбома и артиста, отдача обложки.
- [ ] 5.5 Обновить README/ARCHITECTURE и `hub/API.md` в части каталога Hub.
