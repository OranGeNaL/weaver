## Context

См. `proposal.md — Why`. После этапов 1–6 Hub владеет каталогом, пользователями, ♥, плейлистами, историей и доставкой; PWA работает через `/api/library/*`. Остаётся переходный слой: контейнер ND, `/api/nd/*`, `nd_*`-поля, ND-моки и документация.

## Goals / Non-Goals

**Goals:**
- Полностью убрать Navidrome из кода, схемы, конфигурации, тестов и документации.
- Сделать так, чтобы поиск по репозиторию не находил рабочих упоминаний ND.

**Non-Goals:**
- Не менять поведение каталога, ♥, плейлистов и истории.
- Не добавлять новые функции.
- Не поддерживать старые PWA-сборки.
- Не удалять `nd:`-алиас до проверки, что PWA обновлена (это последний шаг).

## Decisions

### 1. Удаление, а не shim

`/api/nd/*` удаляется, а не перенаправляется: старые сборки PWA получают `404`. Рабочих инстансов нет, поддержка не нужна. Альтернатива — оставить shim — противоречит цели «исключить Navidrome».

### 2. Схема

Одна миграция удаляет (таблично-квалифицированно): `tracks.nd_song_id`, `play_events.nd_song_id`/`nd_album_id`/`nd_artist_id`, `dislikes.nd_song_id`, `wave_items.nd_song_id`, `smart_items.nd_song_id`, `smart_hidden.nd_song_id`, `mix_items.nd_song_id`, `mix_settings.nd_playlist_id`, `mixes.nd_playlist_id`, `import_collections.nd_song_ids`, `favorite_subjects.nd_id`, `users.nd_username`/`nd_password_enc`, `mirror_pending` (после решения об использовании), `import_collections.nd_playlist_id`, `wave_items.key` (аналитика читает `ref`); `navidrome_sync` уже удалена на этапе 5, `wave_sessions.seed_json` остаётся (меняются только ND-ключи чтения), `superseded_scrobble` остаётся; связанные индексы. `play_events.client` больше не документирует `navidrome`. Единый `rg`-шаблон: `nd_song_id|nd_album_id|nd_artist_id|nd_playlist_id|nd_username|nd_password|nd_id|nd_song_ids|navidrome_sync|mirror_pending`. Откат восстанавливает nullable-колонки (без уникальных ограничений); `navidrome_sync` не восстанавливается — ею владеет этап 5. Перед удалением проверяется `rg` по всем перечисленным именам.

### 3. Инфраструктура

Удаляются сервис `navidrome`, том `navidrome-data`, mount `navidrome.toml`, `ND_*`/`NAVIDROME_*`, `PUBLIC_MUSIC_URL`, `hub.depends_on: navidrome`. `PUBLIC_HUB_URL` остаётся. `config.yaml`/`config.example.yaml` не содержат ND-настроек (кроме исторических комментариев backup).

### 4. Код

Удаляются `library/navidrome.py`, `api/medialib.py`, регистрация роутера, ND-циклы/задачи, фейки и respx-маршруты `navidrome.test`. Проверяются все call-sites: `admin_client`, `user_navidrome`, `NavidromeAdmin`, `NavidromeHistory`, `SubsonicError`, `sync_navidrome`, `enqueue_scan`, `resolve_playlist`, `ai/agent.py`, `mixes/wave.py`. Порядок: 6.6 (ручной прогон PWA) → 6.7 (удалить `nd:`/`lib:<int>`, вернуть `404`) → 3.1 (миграция drop). WebView: после 6.6/6.7 убрать `{song_id}`-ветку и оставить `{ref:"lib:<uid>"}` (задача 6.8).

### 5. Health и админ

Из `/healthz` удаляется `navidrome` (и `_navidrome_ok`); из админских ответов — ND-пользователи и `player_seen_at`, если он брался из ND. Удаляются `public_music_url` из `MeOut`/`/api/auth/me`, `music_url` из `/api/auth/options`, `player_seen_at` из админских ответов/схем/веб-типов (включая `Admin.tsx`/`AdminUser.tsx`); кнопка в `More.tsx` уже убрана на этапе 6. Счётчики ♥/плейлистов — из Hub.

### 6. Документация и спеки

Обновляются README, ARCHITECTURE (инвариант №1 и схема), `hub/API.md` (пофайловый чек-лист, ~60 упоминаний) и Purpose базовых спек (`auth`, `library`, `listening`, `storage`, `system`). Проверка docs — по Purpose-строкам с allowlist (имена сценариев/заголовков change), а не `rg` по всему `openspec/specs`.

## Risks / Trade-offs

- [Остались скрытые ссылки на `nd_*`] → `rg` по коду/тестам/веб и падение тестов при обращении.
- [Старая PWA в кэше] → `404` ожидаем; версия shell инвалидируется; документируется требование обновить PWA.
- [Миграция удаляет нужную колонку] → проверка `rg` до миграции, откат с nullable-колонками.
- [Документация расходится] → отдельный чек-лист по README/ARCHITECTURE/API.md.
- [Удаление `nd:`-алиаса раньше времени] → удаляется последним, после ручного прогона PWA.
- [Drop-scope уже, чем схема] → перечень всех объектов в §2 и `rg` по `nd_id|nd_song_ids|navidrome_sync|mirror_pending`.
- [Health/admin/public_music_url забыты] → отдельные задачи 2.5/2.6.
- [Base-спеки противоречат удалению] → REMOVED proxy и MODIFIED catalog/recommendations в этом change.
- [Документация не проверяема] → пофайловый чек-лист и `rg`-критерий.

## Migration Plan

1. Убедиться, что PWA шлёт `lib:` и не вызывает `/api/nd/*` (6.6).
2. Удалить ND-модули, задачи, фейки и тесты; `/api/nd/*` и `nd:`-резолв — после 6.6; `nd:` возвращает `404`.
3. Применить миграцию полного перечня ND-объектов (после 6.6, чтобы старый код не читал удалённые колонки).
4. Удалить health/admin/`public_music_url`/`player_seen_at`.
5. Удалить ND из compose/env/config/docs и обновить Purpose.
6. Прогнать `rg`, `pytest`, `typecheck`, `build`, `docker compose config`.

Откат: восстановить файлы и миграцию из git; колонки восстанавливаются пустыми; ND-сервис можно вернуть, но данных для него нет.

## Open Questions

Нет.
