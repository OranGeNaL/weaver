## Why

Зависит от `navidrome-migration-0-governance` (правила `config.yaml`).

Этапы 1–6 заменяют все функции Navidrome и переводят PWA на Hub. Остаётся переходный слой: контейнер ND, `/api/nd/*`, `nd_*`-поля, ND-специфичные задачи, тесты и документация. Их нужно удалить, иначе зависимость останется в коде и конфигурации.

## What Changes

- **BREAKING** Удалить сервис Navidrome из `docker-compose.yml`, том `navidrome-data`, `navidrome/navidrome.toml` и все `ND_*`/`NAVIDROME_*` переменные; удалить `PUBLIC_MUSIC_URL`.
- Удалить `hub/app/library/navidrome.py`, `hub/app/api/medialib.py` и маршрут `/api/nd/*`.
- Удалить `nd_*`-поля из моделей, схем, API и веб-типов; миграция дропает колонки.
- Удалить ND-специфичные задачи, циклы и фейки (`nd_scan`, `scrobbles_loop`, моки `navidrome.test`).
- Убрать `navidrome` из `/healthz` и административных ответов.
- Обновить README, ARCHITECTURE, `hub/API.md` и базовые спеки после архивации серии.
- **BREAKING** Старые PWA, которые ещё ходят в `/api/nd/*`, получают `404`; поддержка не сохраняется.
- **BREAKING** Удаляются `public_music_url`/`music_url` и `player_seen_at`; остаётся `public_hub_url`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `system`: публичный health больше не сообщает `navidrome`; удаляются `public_music_url`/`music_url` и `player_seen_at`.
- `library`: удаляется переходный слой `/api/nd/*` и `nd_*`-поля; единственный источник каталога — Hub.
- `catalog-acquisition`, `recommendations` и `listening`: финальные дельты без `nd_*`/`lib:<int>`-алиасов.

## Impact

- Код: `hub/app/library/navidrome.py` (удаление), `hub/app/api/medialib.py` (удаление), `hub/app/main.py`, `hub/app/models.py`, `hub/app/api/schemas.py`, `hub/app/api/*`, `hub/app/jobs/downloads.py`, `hub/app/plays.py`, `hub/app/favorites.py`, `hub/app/listening_summary.py`, `hub/app/mixes/{taste,taste_math,taste_snapshot,taste_select,build,smart,core,shadow,evaluate,embedder,wave}.py`, `hub/app/insights/{names,analytics,launches,mixes_view,taste_view,maps}.py`, `web/src/api/types.ts`, `web/src/lib/*`.
- БД: удаление колонок `nd_song_id`, `nd_album_id`, `nd_artist_id`, `nd_playlist_id`, `nd_username`, `nd_password_enc`; миграция обязательна.
- Инфраструктура: `docker-compose.yml`, `.env`, `.env.example`, `config.yaml`, `navidrome/`, README, ARCHITECTURE.
- Клиенты: PWA обновлена на этапе 6; старые сборки не поддерживаются.
- Проверка: `pytest`, `ruff`, `npm run typecheck`, `npm run build`, `rg "navidrome|nd_song_id|/api/nd"`.
- Обратная совместимость: объявлена ломающей; рабочих инстансов нет.
