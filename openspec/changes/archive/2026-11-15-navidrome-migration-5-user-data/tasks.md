## 1. Схема

- [x] 1.1 Добавить `playlists` (`uid` UNIQUE, `owner_id`, `system`), `playlist_items` (`uid`, `position`, `anchor_uid` — uid соседнего item) и `mix_settings.playlist_uid`; проверить `alembic upgrade head`, откат и отсутствие конфликтов.
- [x] 1.2 Перевести `PendingPlaylistAdd` на `playlist_uid` + `anchor_uid`; проверить миграцию и тест вставки по якорю.
- [x] 1.4 Добавить миграцию `play_events.track_uid` (index), маркер `scrobble`/`origin`, uid-алиас в `favorite_subject`; проверить `alembic upgrade head` и откат.
- [x] 1.3 Удалить только `NavidromeSync` и связанные поля синхронизации (`last_scrobble_id`, `favorites_*`); `nd_*`-колонки не трогать до этапа 7; проверить `rg "NavidromeSync|last_scrobble_id"` и отсутствие ссылок.

## 2. Избранное

- [x] 2.1 Сделать журнал `favorites` единственным источником: удалить `reconcile`, bootstrap из `getStarred2` и `ndsync`-события; зафиксировать `/api/starred` (`song_ids`/`album_ids`/`artist_ids`, `refs`, `subjects` + `track_uids`) и capability `hub_liked_v1`; опубликовать его в `/healthz`; проверить тесты `/api/starred` и `/api/star`.
- [x] 2.2 Обеспечить идемпотентность ♥ по `event_id` и сохранение provenance импорта; проверить повторную отправку и импортированный ♥.
- [x] 2.3 Проверить, что `/api/star` не обращается к Navidrome; `rg "user_navidrome"` в пути ♥ пуст.

## 3. Плейлисты

- [x] 3.1 Перевести `api/playlists.py` на Hub-таблицы (создание, список, добавление, удаление по позициям), сохранив `song_ids`/`library_track_ids` как deprecated-алиасы и добавив `uids`; проверить контрактные тесты PWA.
- [x] 3.2 Реализовать переупорядочивание и вставку по якорю с `numeric`-позициями и rebalance; проверить тест параллельных вставок.
- [x] 3.3 Перевести `jobs/downloads.py::process_playlist_adds` и `pending_playlist_adds` на локальные плейлисты; проверить порядок после загрузки.
- [x] 3.4 Перевести `imports/apply.py::sync_playlists` на Hub-плейлисты с сохранением порядка сервиса; проверить тест импорта плейлиста.

## 4. История и скробблы

- [x] 4.1 Реализовать `POST /api/plays/scrobble` с клиентским `event_id`, маркером `scrobble`/`origin`, ключом `track_uid`, окном дедупликации и `superseded_scrobble`; проверить тесты нового скроббла, дубля и вытеснения.
- [x] 4.2 Удалить `NavidromeHistory`, `scrobbles_loop` и импорт из ND; проверить `rg "NavidromeHistory|scrobbles_loop"`.
- [x] 4.3 Перевести сводку прослушивания на скробблы клиентов; проверить тесты `/api/plays/summary`.

## 5. Миксы и волна

- [x] 5.1 Перевести `mixes/core.py` на локальный плейлист (`playlist_uid`) и системные плейлисты; проверить сборку, замену и очистку.
- [x] 5.2 Перевести smart shuffle на `playlist:<uid>` и волну на `artist_uid`/`song_uid`, сохранив `nd_*` как deprecated-алиасы; проверить тесты smart/wave.
- [x] 5.3 Проверить, что очистка миксов не удаляет пользовательские плейлисты; тест на смешанный набор.

## 6. Загрузки и импорт

- [x] 6.1 Перевести `star=true` после импорта на локальное ♥-событие; проверить тест загрузки с ♥.
- [x] 6.2 Перевести `imports/apply.py` на локальные ♥ и плейлисты (новые колонки `import_collections.playlist_uid`/`track_uids`, deprecated `nd_*` — алиасы); проверить тесты импорта Yandex/Spotify.
- [x] 6.3 Проверить, что импорт не вызывает `user_navidrome`/`admin_client`; ожидается отсутствие обращений.
- [x] 6.4 Перевести `api/downloads.py::resolve_playlist`, `ai/agent.py`, `api/smart_shuffle.py`, `insights/mixes_view.py`, `insights/launches.py`, `imports/core.py`, `mixes/wave.py` на локальные плейлисты/артистов; проверить `rg "create_playlist|update_playlist|user_navidrome|nd\.get_artist|_artist_songs|reconcile|favorite:bootstrap"` (только deprecated-слой).
- [x] 6.5 Перевести `api/medialib.py::liked` (`GET /api/nd/liked`) на журнал ♥ Hub без `get_starred2`; заполнять deprecated `nd_playlist_id` алиасом `playlist_uid` у микса и настроек; проверить `/api/nd/liked` и `/api/mixes/settings`.

## 7. Проверка

- [x] 7.1 Прогнать `pytest` (favorites, playlists, plays, imports, mixes, smart, wave); ожидается зелёный прогон.
- [x] 7.2 Прогнать `ruff check` и `ruff format --check`; ожидается без ошибок.
- [x] 7.3 Проверить `alembic upgrade head` и откат всех шагов миграции (`playlists`, `playlist_uid`, `anchor_uid`, `NavidromeSync`), а не только `-1`.
- [x] 7.4 Проверить вручную: ♥, создание плейлиста, добавление с якорем, «Микс дня», скроббл.
- [x] 7.5 Обновить `hub/API.md` и README в части локальных ♥, плейлистов и истории.
