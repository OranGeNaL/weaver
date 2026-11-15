## Context

См. `proposal.md — Why`. После этапов 2–5 Hub умеет хранить блобы, отдавать каталог и владеть ♥/плейлистами. PWA всё ещё читает `/api/nd/*` (`web/src/pages/Home.tsx`, `MediaLibrary.tsx`, `LibraryAlbum.tsx`) и играет библиотечные треки по `ref=nd:<id>`. `/api/nd/*` остаётся переходным слоем до этапа 7.

## Goals / Non-Goals

**Goals:**
- Полный паритет браузерного опыта на Hub-каталоге.
- Единый `uid` в очереди, ссылках и API.
- Возможность отката на `/api/nd/*` по capability.

**Non-Goals:**
- Не удалять `/api/nd/*` и `nd_*`-поля (этап 7).
- Не добавлять офлайн-прослушивание.
- Не менять Android-приложение.
- Не добавлять новые экраны сверх паритета.

## Decisions

### 1. Capability и переключение

`/healthz.capabilities` получает `hub_library_v1`. `web/src/lib/capabilities.ts` проверяет его и выбирает источник каталога; при отсутствии — fallback на `/api/nd/*`. Ветка fallback живёт в `api/client.ts`, а не в страницах: `rg "api.nd"` в страницах пуст. Fallback нормализует формы, а не просто перенаправляет:

| Поле | `/api/library` | `/api/nd` |
|---|---|---|
| ID | `uid` | `id` |
| треки | `tracks[]` (`LibTrackOut`) | `tracks[]`/`songs[]` с `nd_song_id` |
| обложка | `cover_url` по `uid` | `cover_url` → `/api/nd/cover` |
| счётчики | `track_count` | `song_count` |
| сортировка | `sort` | `type` |
| избранное | `track_uids` | `song_ids` |
| ref трека | `lib:<uid>` | `nd:<nd_song_id>` |

Это защищает и закешированную старую PWA на новом сервере, и новую PWA на старом/недоступном `/healthz`.

### 2. Карта endpoint'ов

| Было (`/api/nd`) | Стало (`/api/library`) |
|---|---|
| `/home` | `/home` |
| `/albums?type=` | `/albums?sort=` |
| `/albums/{id}` | `/albums/{uid}` |
| `/artists` | `/artists` |
| `/artists/{id}` | `/artists/{uid}` |
| `/liked` | `/liked` (этап 5) |
| `/cover/{id}` | `/albums/{uid}/cover` |

`type` маппится в `sort` на клиенте или принимается как deprecated-параметр.

### 3. Плеер и очередь

`ListenTrack.nd_song_id` переименовывается в `uid` (с deprecated-алиасом); smart shuffle и события принимают `lib:<uid>`/`uid` наряду с `nd_song_id`; `lib:<uid>`/`uid` резолвится в `tracks.uid` до `Bpmap`/`is_ref`, чтобы события и рекомендации не получали `ref="lib:<uid>"` с `nd_song_id=NULL`. `ref` библиотечного трека — `lib:<uid>`. `listen.py` резолвит `lib:` по `tracks.uid` и MUST принимать legacy `lib:<int>` до этапа 7. Все эмиттеры `lib:` переводятся на `uid`, включая `ai/pipeline.py` (сейчас отдаёт `lib:<int>`); очередь и `playQueue` используют `lib:<uid>`. Для WebView-моста библиотечный элемент уходит как `{ref: "lib:<uid>"}`: `playQueue` уже принимает ref-элементы, приложение играет их через Hub; `{song_id: nd_song_id}` остаётся deprecated до этапа 7. Выбор фиксируется в `player.ts` и проверяется тестом payload.

### 4. Обложки

`/api/library/albums/{uid}/cover?size=` отдаёт из кэша/S3; PWA перестаёт использовать `/api/nd/cover`. Старый endpoint остаётся deprecated.

### 5. Удаление кнопки Navidrome

`More.tsx` больше не показывает кнопку «Navidrome». `PUBLIC_MUSIC_URL` остаётся в конфиге, но PWA его не использует; удаление — этап 7.

### 6. Закешированная PWA

Service worker кеширует оболочку; после выката новый `BUILD_ID` инвалидирует shell. `hub_library_v1` покрывает случай, когда новый фронтенд оказался на старом сервере.

### 7. Мобильный вариант

`listenStreamUrl` MUST передавать `q=mobile` при `navigator.connection.saveData` или `effectiveType` `2g`/`3g`; иначе `q` не передаётся. Это делает `q=mobile` реальным, а не только серверным обещанием.

## Risks / Trade-offs

- [Смена `nd_song_id` → `uid` ломает очередь] → deprecated-алиас и тесты плеера; capability-переключение.
- [Разные форматы `type`/`sort`] → маппинг на клиенте и deprecated-параметр на сервере.
- [Кэш обложек по старым URL] → новые URL, `Cache-Control`, версия сборки.
- [Старая PWA в кэше] → capability fallback и инвалидация shell.
- [Дублирование endpoint'ов] → ограничено этапами 6–7.
- [`lib:`-эмиттер остаётся на int] → задача 1.2 и `rg "lib:"`; legacy `lib:<int>` временно резолвится.
- [Спека требует `/api/nd` всегда, а design — fallback] → спека условная: с capability запрет, без — deprecated fallback.
- [`q=mobile` не отправляется] → задача 3.3 и тест `listenStreamUrl`.
- [Blast radius `nd_song_id` шире, чем `listen.ts`] → добавить `Player.tsx`, `MiniPlayer.tsx`, `catalog.tsx`, `TrackMenu.tsx`, `stars.ts`, `LibraryAlbum.tsx`, `MediaLibrary.tsx`.
- [Fallback возвращает другую форму] → таблица маппинга и unit-тест обеих веток.

## Migration Plan

1. Добавить `hub_library_v1`, `lib:<uid>`-эмиттеры и резолв (включая legacy `lib:<int>`).
2. Переключить страницы и типы PWA; ветка `/api/nd` — в `api/client.ts`.
3. Переключить плеер и очередь.
4. Убрать кнопку Navidrome и включить `q=mobile`.
5. Проверить `typecheck`/`build` и ручной прогон.

Откат: убрать `hub_library_v1` из `/healthz`, PWA вернётся на `/api/nd/*`; серверный код не удаляется до этапа 7.

## Open Questions

Нет.
