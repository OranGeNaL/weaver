## Why

Зависит от `navidrome-migration-0-governance` (правила `config.yaml`).

Сейчас ♥, плейлисты и история скробблов принадлежат Navidrome, а Hub только синхронизирует и сверяет их. После отказа от ND эти данные должны жить в Hub: иначе PWA теряет «Понравившиеся», плейлисты, «Микс дня» и статистику. Рабочих инстансов нет, поэтому перенос не нужен — достаточно спроектировать владение и API.

## What Changes

- Сделать журнал ♥ (`favorites`) единственным источником истины; удалить сверку и bootstrap из Navidrome.
- Ввести таблицы плейлистов Hub (`playlists`, `playlist_items`) с порядком и **якорями вставки** для фоновых добавлений.
- Принимать скробблы от клиентов Hub; удалить импорт скробблов и `NavidromeSync`.
- Перевести «Микс дня», «Мою волну» и smart shuffle на локальные плейлисты и `uid` артиста.
- Перевести импорт коллекций (Yandex/Spotify export) на ♥ и плейлисты Hub.
- Перевести `star=true`, `pending_playlist_adds` и `MixSettings` на локальные сущности.
- Сохранить `/api/playlists`, `/api/starred`, `/api/star` как контракт PWA; идентификаторы плейлистов становятся `uid`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `listening`: ♥ и дизлайки принадлежат Hub; скробблы Navidrome заменяются приёмом скробблов клиентов; сводка больше не зависит от ND.
- `library`: «Плейлисты Navidrome» заменяются «Плейлистами Hub» с порядком и якорями.
- `recommendations`: «Микс дня» и настройки используют локальный плейлист; волна по артисту — `uid`.
- `catalog-acquisition`: загрузка и импорт коллекции пишут ♥ и плейлисты в Hub, а не в ND.
- `system`: в `/healthz.capabilities` появляется `hub_liked_v1`.

## Impact

- Код: `hub/app/favorites.py`, `hub/app/plays.py`, `hub/app/listening_summary.py`, `hub/app/api/library.py`, `hub/app/api/playlists.py`, `hub/app/api/starred.py` (если есть), `hub/app/imports/apply.py`, `hub/app/imports/core.py`, `hub/app/jobs/downloads.py`, `hub/app/mixes/core.py`, `hub/app/mixes/smart.py`, `hub/app/mixes/wave.py`, `hub/app/mixes/build.py`, `hub/app/models.py`.
- БД: `playlists`, `playlist_items`, изменения `favorite_subject`/`play_events`/`mix_settings`; миграция обязательна.
- API: `/api/playlists`, `/api/starred`, `/api/star` сохраняются; `nd_playlist_id`/`nd_song_id` — deprecated до этапа 7.
- Фоновые задачи: `scrobbles_loop` удаляется; `playlist_adds_loop` работает с локальными плейлистами.
- Клиенты: PWA продолжает работать; переключение страниц — этап 6.
- Проверка: `pytest` (♥, плейлисты, импорт, миксы, история), `ruff`.
- Обратная совместимость: идентификаторы плейлистов меняются на `uid`, но остаются непрозрачными строками для PWA.
