## 1. Hub: доставка и capability

- [x] 1.1 Добавить `hub_library_v1` в `/healthz.capabilities`; проверить ответ и `web/src/lib/capabilities.ts`.
- [x] 1.2 Реализовать резолв `ref=lib:<uid>` в `listen.py` и `api/listen.py`, включая legacy `lib:<int>` на время этапа 6; перевести эмиттеры `lib:` (`ai/pipeline.py`, `*Out`) на `uid`; проверить тесты библиотечного трека, старого `lib:<int>` и `404`.
- [x] 1.3 Сохранить deprecated-резолв `ref=nd:<id>` → `uid`; проверить тест совместимости.
- [x] 1.4 Проверить `/api/library/albums/{uid}/cover?size=` из кэша/S3 (реализация — change 3); тесты ресайза, `404`, `Cache-Control`.

## 2. PWA: каталог

- [x] 2.1 Перевести `Home.tsx`, `MediaLibrary.tsx` (`NdAlbumPage`/`NdAlbumsPage`), `Library.tsx` на `/api/library/*`; `LibraryAlbum.tsx` перевести на `uid` в маршруте, ссылках, обложке и `api.libAlbum(uid)`; проверить `npm run typecheck`.
- [x] 2.2 Обновить `api/client.ts` и `api/types.ts`: `uid`, новые endpoint'ы, deprecated-алиасы; fallback-адаптер в `client.ts` нормализует формы по таблице design §1; добавить unit-тест обеих веток.
- [x] 2.5 Добавить vitest и `npm run test` (обязательно); проверить `npm run test`.
- [x] 2.3 Перевести `Playlists.tsx` и страницу плейлиста на `uid`; проверить создание, добавление и удаление.
- [x] 2.4 Убрать кнопку «Navidrome» из `More.tsx` и текст «плейлисты в Navidrome»; проверить `rg "public_music_url" web/src` (только типы/конфиг, без рендера).

## 3. PWA: плеер

- [x] 3.1 Перевести `lib/listen.ts`, `lib/player.ts`, `pages/Player.tsx`, `pages/LibraryAlbum.tsx`, `pages/MediaLibrary.tsx`, `components/MiniPlayer.tsx`, `components/catalog.tsx`, `components/TrackMenu.tsx` и `lib/stars.ts` на `lib:<uid>`/`uid`; проверить очередь, перемотку, следующий трек и ♥.
- [x] 3.2 Зафиксировать payload WebView-моста `{ref:"lib:<uid>"}`; `{song_id: nd_song_id}` — deprecated до этапа 7; проверить тест старого формата и payload.
- [x] 3.4 Научить `smart shuffle`, `plays` и `api/plays.py` принимать `lib:<uid>`/`uid`, резолвить в `tracks.uid` до `Bpmap`/`is_ref`; проверить `web/src/lib/smartShuffle.ts`, `lib/listen.ts`, `hub/app/api/smart_shuffle.py`, `hub/app/plays.py`, `hub/app/api/plays.py`.
- [x] 3.3 Реализовать выбор `q=mobile` в `listenStreamUrl` по `navigator.connection.saveData`/`effectiveType`; проверить тест URL и `X-HomeMusic-Variant` для библиотечного трека из S3-кэша.

## 4. Проверка

- [x] 4.1 Прогнать `npm run test`, `npm run typecheck`, `npm run build`, `ruff check` и `ruff format --check`; ожидается без ошибок.
- [x] 4.2 Прогнать `pytest hub/tests/test_listen.py hub/tests/test_medialib.py` и новые тесты change; ожидается зелёный прогон.
- [x] 4.3 Проверить вручную: главная, все альбомы, артист, альбом, плейлист, воспроизведение и перемотка.
- [x] 4.4 Проверить обе ветки: старая PWA без capability → `/api/nd/*` и `nd:<id>`; новая PWA против `/healthz` без `hub_library_v1` и с таймаутом `/healthz` → fallback.
- [x] 4.5 Обновить `hub/API.md` и README в части `hub_library_v1` и `lib:<uid>`.
