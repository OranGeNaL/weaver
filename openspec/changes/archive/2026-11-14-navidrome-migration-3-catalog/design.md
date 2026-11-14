## Context

См. `proposal.md — Why`. После этапов 1–2 есть Postgres и S3-блобы. Сейчас каталог для PWA приходит из `/api/nd/*` (Navidrome), а Hub хранит только `albums`/`tracks`, созданные импортом. Сканера бакета по решению Q7 нет: всё попадает через Hub.

## Goals / Non-Goals

**Goals:**
- Собственный каталог Hub с публичными `uid`, артистами и обложками.
- Замена `nd_scan` на детерминированную фиксацию каталога.
- Совместимость: `/api/nd/*` работает до этапа 6.

**Non-Goals:**
- Не переносить ♥, плейлисты и историю (этап 5).
- Не переключать PWA (этап 6).
- Не удалять `nd_*`-поля и `/api/nd/*` (этап 7).
- Не индексировать объекты, появившиеся в бакете в обход Hub.

## Decisions

### 1. Модель артиста

Таблица `artists(id, uid, name, name_norm, sort_name, cover_blob_id, created_at)`, `albums.artist_id`. `name_norm = normalize_artist(name).latin or normalize_artist(name).text` — уникальный ключ с `CHECK (name_norm <> '')`; индекс под `alphabeticalByArtist`. UNIQUE делает однофамильцев неразделимыми: это осознанный компромисс; ручное слияние/разделение — отдельная операция. Создание — `INSERT ... ON CONFLICT (name_norm) DO UPDATE/NOTHING`, чтобы параллельные импорты не плодили дубли. Артист создаётся лениво при импорте по нормализованному имени (`normalize_artist`). `tracks.artist` и `albums.album_artist` остаются денормализованными кредитами. Слияние — отдельная операция, переносящая альбомы и треки. Альтернатива — вычисляемая проекция — отклонена: нужны `uid`, обложка и ♡ артиста.

### 2. Публичный ID

`uid` — единственный публичный идентификатор для новых endpoint'ов и ответов. Pydantic-схемы `*Out` отдают `uid`. До этапа 7 переходный слой MAY принимать/возвращать deprecated `int`-поля (`id`, `album_id`, `track_id`) и `nd_*`, чтобы текущая PWA не ломалась; новые поля — только `uid`. Это исключение фиксируется в спеке и снимается на этапе 7.

### 3. Запросы каталога

- `newest` — по `albums.created_at`.
- `recent`/`frequent` — из агрегата `play_events`; при отсутствии данных пусто.
- `starred` — из свёртки `play_events` kind like/unlike (журнал уже есть); при отсутствии данных пусто.
- `alphabeticalByName`/`alphabeticalByArtist` — `lower(name)` с учётом кириллицы.
- `random` — `random()` Postgres.
- Пагинация — `limit/offset`, лимит **200**; **дефолт до этапа 6 — `updated_at`** (как текущая PWA), затем можно сменить на `alphabeticalByName` с объявлением изменения.

### 4. Импорт только через Hub

Новые файлы появляются только через: downloads, uploads/commit, ассистент, административный `POST /api/library/import` (bootstrap). Bootstrap принимает файлы/пути из смонтированного каталога и вызывает тот же `importer`, что и остальные пути; прямого сканирования бакета нет. Задача `catalog_reconcile` только сверяет и чистит, но не индексирует новые объекты.

### 5. Замена `nd_scan`

После импорта выполняется `catalog_commit` в одной транзакции: блоб `committed` → строка `tracks` → `library_track_id`/`uid` элемента загрузки → `done`. Ключ идемпотентности — `(download_item_id, sha256)`, для него добавляется `download_items.blob_sha256` (без UNIQUE; уникальность — `(download_item_id, blob_sha256)`); не только `sha256`, потому что два легитимных трека могут делить блоб. `nd_sync` ставится в очередь после коммита, коалесцируется и идемпотентен по элементу. `catalog_commit` сохраняет только Hub-side effects `nd_scan` до этапа 5 через `nd_sync`: `imports_done`, `mixes_done`; ND-записи (`star_liked`, `sync_likes`, `sync_playlists`, `process_playlist_adds`) **инертны** с этапа 3, пока нет `nd_song_id`/кредов, и переносятся на локальные ♥/плейлисты на этапе 5. `nd_sync` MUST NOT падать/зацикливаться на неллинкованных элементах. На этапе 5 эффекты переводятся на локальные ♥/плейлисты, на этапе 7 `nd_sync` удаляется.

### 6. Переходный слой `/api/nd/*`

До этапа 6 `/api/nd/*` остаётся без изменений и продолжает читать Navidrome (он ещё запущен). Новый `/api/library/*` строится параллельно. На этапе 6 PWA переключается, на этапе 7 `/api/nd/*` удаляется. Альтернатива — сразу перенаправить `/api/nd/*` на Hub — рискованна: PWA потеряет паритет до готовности этапа 6.

### 7. Обложки

Обложка — блоб; `albums.cover_blob_id`. Ресайзы кэшируются по `(sha256, size)`. Fallback — встроенная обложка первого трека, затем заглушка.

### 8. События

Имена событий `library` и их payload не меняются: `kind`, `album_id`, `track_id`, `uid`. `album_id`/`track_id` остаются deprecated `int` до этапа 7; новые потребители читают `uid`. SSE-контракт сохраняется.

### 9. Совместимость PWA

Текущая PWA вызывает `/api/library/albums/{id}`, `/tracks/{id}`, `/albums/{id}/cover`, `merge`, `move` с `int`. До этапа 7 включительно Hub MUST принимать и `uid`, и legacy `int` (dual lookup) в этих маршрутах, включая cover/patch/delete; `GET`-ответы MAY содержать оба поля. Это снимает утверждение «PWA продолжает работать только через `/api/nd/*`».

### 10. Сверка и GC

`catalog_reconcile` — только детектив: `tracks.integrity_issue` = `missing_blob`, `albums.integrity_issue` = `missing_cover`, `orphan_blob` — в health/лог; сбрасывает признаки при успешной сверке; запускается housekeeping-расписанием. Удаление блобов остаётся за GC домена `storage` (этап 2). Это исключает двух владельцев удаления.

## Risks / Trade-offs

- [Ошибки дедупликации артистов] → нормализация + ручное слияние; тесты на «Кино»/«кино» и однофамильцев.
- [`recent`/`frequent`/`starred` пусты до этапа 5] → явно описано в спеке и UI; не блокирует каталог.
- [Дублирование `/api/nd/*` и `/api/library/*`] → переходный период ограничен этапами 3–6; на 7 удаляется.
- [`catalog_commit` гоняется с GC] → блокировка по `sha256` и статусы блоба; тест на параллельный импорт.
- [Bootstrap-импорт обходит uploads] → admin-only, тот же `importer`, лимиты и проверки.
- [PWA на int-ID ломается] → dual lookup `uid`/`int` до этапа 6; тест старого вызова.
- [Артисты-дубли] → `name_norm` UNIQUE + upsert; тест параллельного импорта.
- [Reconcile и storage GC удаляют одно и то же] → reconcile только помечает, удаляет storage GC.
- [Пустые `recent`/`frequent`/`starred` до 5] → change 5 добавляет живой `/api/library/liked`; главная читает журнал `play_events` kind like/unlike + `favorite_subjects`.
- [`catalog_commit` теряет side-effects] → `nd_sync` до этапа 5, ключ `(download_item_id, sha256)`; ND-записи инертны до этапа 5 и не считаются ошибкой.

## Migration Plan

1. Добавить `artists` (`name_norm` UNIQUE), `albums.artist_id`, `tracks.integrity_issue`, ссылки на блобы; миграция.
2. Реализовать `/api/library/*` с `uid` и dual lookup `int`/`uid`.
3. Заменить `nd_scan` на `catalog_commit` в одной транзакции.
4. Добавить bootstrap-импорт и `catalog_reconcile` (только детектив).
5. Перевести обложки на блобы.
6. Проверить, что `/api/nd/*` продолжает работать.

Откат: вернуть `nd_scan` и старые ссылки; новые таблицы/поля остаются неиспользуемыми.

## Open Questions

Нет.
