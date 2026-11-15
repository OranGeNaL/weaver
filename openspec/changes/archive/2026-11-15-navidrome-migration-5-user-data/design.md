## Context

См. `proposal.md — Why`. Сейчас `favorites.py` ведёт журнал решений, но сверяется с Navidrome (`reconcile`, bootstrap, `NavidromeSync`); `api/playlists.py` — прокси ND; `plays.py` импортирует скробблы; `imports/apply.py` и `jobs/downloads.py` пишут ♥ и плейлисты в ND; `mixes/core.py` держит `nd_playlist_id`. После этапов 1–4 есть Postgres, каталог и локальные учётки.

## Goals / Non-Goals

**Goals:**
- Единственный владелец пользовательских данных — Hub.
- Сохранить контракт PWA (`/api/playlists`, `/api/starred`, `/api/star`).
- Корректный порядок при асинхронных добавлениях.

**Non-Goals:**
- Не удалять `nd_*`-поля из API (этап 7).
- Не переключать PWA на новые страницы (этап 6).
- Не добавлять умные плейлисты по правилам.
- Не переносить данные из ND.

## Decisions

### 1. ♥ — единственный источник истины

`favorites.py` остаётся журналом типизированных решений; удаляются `reconcile`, bootstrap из `getStarred2`, `ndsync`-события и `NavidromeSync`. Импортированные ♥ получают `provenance: "import"`. Алиасы строятся по `uid` и `ref`. Команда ♥ идемпотентна по `event_id`; повтор не переигрывает старое решение.

### 2. Плейлисты Hub

Таблицы:

```
playlists(id, uid, owner_id, name, system, created_at, updated_at)
playlist_items(id, uid, playlist_id, track_uid, position, anchor_uid, added_by, added_at)
```

`position` — `numeric`, чтобы вставлять между соседями без переписывания всех строк; при исчерпании зазора выполняется rebalance. Дубликаты разрешены. `anchor_uid` — `uid` соседнего элемента (не трека) для фоновой вставки. Уникальность — `(playlist_id, uid)`; отдельного уникального ключа по треку нет. У системных плейлистов (`system=true`, «Микс дня») `owner_id` MUST быть задан пользователем: список показывает только свои плейлисты и свои системные, а не все системные подряд.

### 3. Скробблы

Клиенты отправляют скробблы в Hub в `POST /api/plays/scrobble` (отдельный endpoint, не «или events»). Скроббл хранится с явным маркером `scrobble=true`/`origin=client`, ключом `track_uid` и временем. Дедупликация — по `event_id`, затем по `track_uid` в окне `DEDUP_WINDOW + длительность`; вытесненный скроббл сохраняется с `superseded_scrobble=true` для сводки. `NavidromeHistory`, `scrobbles_loop`, `NavidromeSync.last_scrobble_id` удаляются.

### 4. Миксы и волна

`MixSettings.nd_playlist_id` → `playlist_uid`. «Микс дня» пишет в локальный плейлист с `owner_id` пользователя; очистка удаляет только свои `system=true`-плейлисты. Smart shuffle принимает `playlist:<uid>`; волна по артисту — `nd_artist_id` → `artist_uid`; `seed.nd_song_id` → `seed.song_uid`. Старые `nd_*` остаются deprecated-алиасами до этапа 7.

### 5. Загрузки и импорт

`PendingPlaylistAdd.playlist_id` → `playlist_uid` + `anchor_uid`. `star=true` после импорта создаёт локальное ♥-событие, а не вызов ND. Импорт коллекций пишет ♥ и плейлисты в Hub, сохраняя порядок сервиса.

`import_collections` получает честные колонки `playlist_uid` и `track_uids` (JSONB): они — источник для Hub-плейлиста импорта. Deprecated `nd_playlist_id`/`nd_song_ids` заполняются теми же значениями до этапа 7, когда будут удалены.

### 6. API и совместимость

`PlaylistOut.id` — `uid`; `PlaylistPageOut.tracks` — треки по `uid`. PWA воспринимает id как непрозрачную строку, поэтому смена формата безопасна, но входные поля сохраняются: `song_ids` (deprecated, резолвится через `nd_song_id` до этапа 7), `library_track_ids`, новые `uids`. `/api/starred` MUST сохранять прежние `song_ids`/`album_ids`/`artist_ids`, `refs` (включая pending/catalog-only) и `subjects` с provenance, добавлять `track_uids`; вводится capability `hub_liked_v1` и публикуется в `/healthz`. `nd_song_id`/`nd_playlist_id` остаются deprecated-полями до этапа 7.

`GET /api/nd/liked` (`app/api/medialib.py`) тоже обслуживается Hub: он строится из журнала ♥ (`favorites.snapshot`), а не из `getStarred2`, поэтому форма `NdLikedOut` сохраняется, но Navidrome в пути ♥ не участвует.

`MixSettings.nd_playlist_id` и `Mix.nd_playlist_id` — deprecated-алиасы: они MUST заполняться тем же значением, что и `playlist_uid`, чтобы старый клиент видел непустое поле до этапа 7.

### 7. Транзакции и идемпотентность

Одна транзакция на команду: решение ♥ + событие; вставка в плейлист + позиция; импорт элемента. Внешней синхронизации нет, поэтому outbox не нужен. Повторные запросы идемпотентны по `event_id`/`operation`; вставка по якорю защищена блокировкой плейлиста.

### 8. Модули, которые тоже пишут в ND

Кроме `api/playlists.py`, на локальные сервисы переводятся: `api/downloads.py::resolve_playlist`, `ai/agent.py` (создание/добавление/удаление/переименование плейлистов и `starred`), `api/smart_shuffle.py` (`update_playlist`), `insights/mixes_view.py`, `insights/launches.py`, `imports/core.py`, `mixes/wave.py`, `mixes/taste.py`, `mixes/taste_math.py` (вызовы `favorites.reconcile` и `favorite:bootstrap`). (волна по артисту читает локальные `artists`/`tracks` по `artist_uid`, без `get_artist`/`_artist_songs`). После этапа 5 `rg "create_playlist|update_playlist|user_navidrome|nd\.get_artist|_artist_songs"` в этих путях пуст (Deezer `get_artist` не считается).

Сюда же входит `api/medialib.py::liked` (`GET /api/nd/liked`): он собирается из журнала ♥ Hub, а `get_starred2` в пути ♥ не вызывается.

## Risks / Trade-offs

- [Конкурентные вставки ломают порядок] → `numeric`-позиции, блокировка плейлиста, rebalance; тест параллельных вставок.
- [Дубликаты в плейлистах] → разрешены по спеке; удаление по позиции, не по треку.
- [Импорт создаёт дубли ♥] → provenance + идемпотентность по `source_map`.
- [Миксы удаляют пользовательские плейлисты] → удалять только `system=true` и только свои.
- [Смена id плейлиста ломает PWA] → id непрозрачен; контрактные тесты PWA.
- [Старые `nd_*`-поля расходятся] → заполнять deprecated-алиасы до этапа 7.
- [Модули вне impact продолжают писать в ND] → §8 и `rg`-проверка.
- [Системные плейлисты видны всем] → `owner_id` у системных + фильтр.
- [Скроббл без маркера ломает сводку] → `scrobble=true`/`superseded_scrobble` и тест.
- [Удаление `nd_*` раньше 7] → 1.3 удаляет только `NavidromeSync`, колонки — этап 7.

## Migration Plan

1. Добавить `playlists`/`playlist_items`, `playlist_uid` в `mix_settings`, `anchor_uid` в pending-adds; миграция.
2. Перевести ♥ на локальный источник, удалить reconcile/bootstrap; зафиксировать `/api/starred` и `hub_liked_v1`.
3. Перевести API плейлистов на Hub с алиасами входных полей.
4. Перевести импорт, загрузки, миксы, волну и smart shuffle; добавить модули из §8.
5. Добавить приём скробблов, удалить цикл импорта.
6. Прогнать тесты.

Откат: вернуть ND-провайдеры из git; локальные таблицы остаются неиспользуемыми.

## Open Questions

Нет.
