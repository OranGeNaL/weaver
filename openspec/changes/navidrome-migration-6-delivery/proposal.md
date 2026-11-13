## Why

Зависит от `navidrome-migration-0-governance` (правила `config.yaml`).

PWA — основной клиент, но её экраны библиотеки, плейлистов и плеера до сих пор читают `/api/nd/*` и используют `nd_song_id`. Каталог, ♥, плейлисты и история уже в Hub (этапы 3–5), S3-доставка есть (этап 2). Осталось переключить PWA на Hub-каталог и убрать зависимость от Navidrome в браузерном опыте.

## What Changes

- Переключить `Home`, `MediaLibrary`, `Library`, `LibraryAlbum`, `Playlists`, `More` на `/api/library/*`.
- Перевести очередь и плеер PWA на `uid`; библиотечные треки — `ref=lib:<uid>`; `nd:` остаётся deprecated-алиасом до этапа 7.
- Отдавать обложки из `/api/library/albums/{uid}/cover` с ресайзом.
- Обеспечить `q=mobile` для библиотечных треков из S3-кэша; `nd_song_id` остаётся deprecated-алиасом до этапа 7.
- Убрать кнопку «Navidrome» и зависимость PWA от `PUBLIC_MUSIC_URL`.
- Добавить capability `hub_library_v1`, чтобы клиенты выбирали источник каталога без угадывания.
- `/api/nd/*` остаётся в коде как deprecated и удаляется на этапе 7.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `listening`: подбор библиотечного трека и потоковая отдача работают по `lib:<uid>` и S3-кэшу; `nd_song_id` остаётся deprecated-алиасом до этапа 7.
- `library`: PWA получает каталог и обложки из Hub; `/api/nd/*` больше не используется клиентом.
- `system`: в `/healthz.capabilities` появляется `hub_library_v1`.

## Impact

- Код web: `web/src/pages/Home.tsx`, `MediaLibrary.tsx`, `Library.tsx`, `LibraryAlbum.tsx`, `Playlists.tsx`, `More.tsx`, `web/src/api/client.ts`, `types.ts`, `web/src/lib/listen.ts`, `player.ts`, `stars.ts`, `web/src/pages/Player.tsx`, `web/src/components/MiniPlayer.tsx`, `catalog.tsx`, `TrackMenu.tsx`.
- Код Hub: `hub/app/listen.py`, `hub/app/api/listen.py`, `hub/app/api/medialib.py` (deprecated), `hub/app/api/system.py`, `hub/app/api/library.py`.
- API: новые вызовы `/api/library/*`; `/api/nd/*` не используется PWA при `hub_library_v1` (без capability — deprecated fallback); `nd:`-ref остаётся алиасом.
- Клиенты: PWA в браузере и в WebView; Android вне серии.
- Проверка: `npm run typecheck`, `npm run build`, `pytest` (listen/medialib/system), ручной прогон PWA.
- Обратная совместимость: `nd:`-ref и `/api/nd/*` сохраняются до этапа 7.
