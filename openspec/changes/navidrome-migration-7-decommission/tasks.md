## 1. Подготовка

- [x] 1.1 Проверить, что PWA использует `lib:<uid>` и `/api/library/*`; `rg "/api/nd|nd_song_id" web/src` пуст (кроме deprecated-ветки в `api/client.ts`).
- [x] 1.2 Проверить, что резолв `nd:` больше не нужен; составить список ссылок `rg "navidrome|Navidrome|nd_song_id|nd_album_id|nd_artist_id|nd_playlist_id" hub/app web/src`.
- [x] 1.3 Проверить, что все тесты зелёные до удаления; зафиксировать базовый прогон.

## 2. Удаление кода

- [x] 2.1 Удалить `hub/app/library/navidrome.py`, `hub/app/api/medialib.py` и регистрацию роутера; проверить `rg "medialib|NavidromeClient|admin_client|user_navidrome|NavidromeAdmin|NavidromeHistory|SubsonicError"`.
- [x] 2.2 Удалить ND-задачи/циклы (`nd_scan`, `scrobbles_loop`, `NavidromeSync`, `sync_navidrome`, `enqueue_scan`); `nd:`-резолв и legacy `lib:<int>` в `listen.py` удалить в 6.7 после 6.6; проверить `rg`.
- [x] 2.3 Удалить `nd_*`-поля из моделей, схем, API и веб-типов, включая `nd_song_ids`, `import_collections.nd_playlist_id`, `favorite_subjects.nd_id`, `mirror_pending` (но **не** `superseded_scrobble`), `dislikes.nd_song_id`, `wave_items`/`smart_items`/`smart_hidden`/`mix_items.nd_song_id`, `mix_settings`/`mixes.nd_playlist_id`; `navidrome_sync` уже удалена на этапе 5, `wave_sessions.seed_json` остаётся; проверить `rg "nd_song_id|nd_album_id|nd_artist_id|nd_playlist_id|nd_username|nd_password|nd_song_ids|nd_id|navidrome_sync|mirror_pending"` (пусто).
- [x] 2.4 Удалить ND-пользователей и `player_seen_at` из админских ответов; проверить тесты `/api/admin/users`.
- [x] 2.5 Удалить `navidrome`/`_navidrome_ok` из `/healthz` и `/api/system/health`, `HealthOut` и веб-типов; проверить тесты system.
- [x] 2.6 Удалить `public_music_url` из `MeOut`/`/api/auth/me`, `music_url` из `/api/auth/options`, `player_seen_at` из админских схем и `Admin.tsx`/`AdminUser.tsx`; проверить `rg "public_music_url|music_url|player_seen_at"`.
- [x] 2.7 Перевести `mixes/*`, `insights/*`, `listening_summary.py`, `favorites.py` с `nd_*`-ключей на `ref`/`uid`; `wave_sessions.seed_json` не дропать, читать `artist_uid`; проверить `rg "nd_song_id|nd_artist_id|nd_playlist_id" hub/app/mixes hub/app/insights hub/app/favorites.py hub/app/listening_summary.py` (пусто).

## 3. Схема

- [x] 3.1 Создать миграцию удаления полного таблично-квалифицированного перечня из design §2 (после 2.7, без `navidrome_sync` и `wave_sessions.seed_json`); проверить `alembic upgrade head` на пустой БД.
- [x] 3.2 Проверить откат миграции: колонки восстанавливаются nullable, приложение не падает; `navidrome_sync` не восстанавливается.
- [x] 3.3 Проверить, что после миграции модели и БД согласованы (`alembic check` или эквивалент).
- [x] 3.4 Проверить, что `navidrome_sync` и `wave_sessions.seed_json` не дропаются повторно; проверить миграцию и `rg`.

## 4. Инфраструктура и конфигурация
- [x] 4.1 Удалить сервис `navidrome`, том `navidrome-data`, mount `navidrome.toml` и `depends_on`; проверить `docker compose config`.
- [x] 4.2 Удалить `ND_*`/`NAVIDROME_*` и `PUBLIC_MUSIC_URL` из `.env`, `.env.example`, `config.py`, `config.yaml`, `config.example.yaml`; проверить `rg`.
- [x] 4.3 Удалить каталог `navidrome/`, ND-слой `web/src` (страницы/типы) и поднять версию shell/`BUILD_ID`, чтобы закешированная PWA перезагрузилась; проверить `rg "navidrome"` (пусто, кроме архива).

## 5. Тесты и документация

- [x] 5.1 Удалить ND-моки/`respx`-маршруты `navidrome.test` и тесты, проверяющие ND; проверить `rg "navidrome.test"` (пусто).
- [x] 5.2 Обновить `hub/API.md` пофайловым чек-листом: убрать `/api/nd/*`, `nd_*`, `public_music_url`; описать `/api/library/*` и `lib:<uid>`; проверить `rg` по каждому разделу.
- [x] 5.3 Обновить README/ARCHITECTURE: схема без ND, инвариант «Hub — источник истины», Postgres/S3.
- [x] 5.4 Обновить Purpose базовых спек с явным списком: `auth/spec.md:3`, `library/spec.md:3`, `listening/spec.md:3`, `storage/spec.md:3`, `system/spec.md:3`; проверить именно эти строки (`rg -i "navidrome" ... | rg "Purpose"`), allowlist — имена сценариев/заголовков change.

## 6. Проверка

- [x] 6.1 Прогнать `pytest`; ожидается зелёный прогон без ND-фейков.
- [x] 6.2 Прогнать `ruff check` и `ruff format --check`; ожидается без ошибок.
- [x] 6.3 Прогнать `npm run typecheck` и `npm run build`; ожидается без ошибок.
- [x] 6.4 Проверить `docker compose config` и запуск стека без сервиса Navidrome.
- [ ] 6.5 Проверить `rg -i "navidrome|nd_song_id|nd_album_id|nd_artist_id|nd_playlist_id|navidrome_sync|mirror_pending"` по репозиторию; допустимы архивные changes, Purpose-строки и `hub/migrations/versions/` (историю не переписываем).
- [ ] 6.6 Проверить вручную: вход, каталог, плейлист, воспроизведение, ♥, «Микс дня», история.
- [x] 6.7 Удалить `nd:`/`lib:<int>`-резолв после 6.6, вернуть `404`; проверить `rg "nd:|lib:<int>" hub/app/mixes hub/app/ai` (пусто).
- [x] 6.8 Убрать `{song_id}`-ветку WebView-моста (`playQueue` и legacy `play(idsJson)`), оставить `{ref:"lib:<uid>"}`; проверить `web/src/lib/player.ts` и typecheck.
