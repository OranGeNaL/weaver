# Архитектурная карта HomeMusic

> Составлено по состоянию репозитория `weaver` (ветка `master`, коммит `509bb43`).
> Основные источники: `docker-compose.yml`, `hub/app/**`, `embedder/app/**`, `web/src/**`,
> `hub/API.md`, `README.md`, `config.example.yaml`, `.env.example`.

---

## 0. Краткое резюме

HomeMusic — это персональный музыкальный сервис, развёрнутый на домашнем ПК в Docker Compose. Он объединяет три крупные подсистемы:

| Подсистема | Технологии | Назначение |
|---|---|---|
| **Navidrome** | готовый образ `deluan/navidrome` | библиотека, стриминг, пользователи, плейлисты, Subsonic API |
| **Hub** | Python 3.12, FastAPI, SQLAlchemy async, SQLite, Alembic | поиск, загрузки, импорт, теги, прослушивание, ИИ-ассистент, рекомендации, аналитика |
| **Embedder** | Python, FastAPI, PyTorch, MuQ-MuLan | аудио- и текстовые векторы для «Микса дня», «Моей волны», карт вкуса |

Доступ из интернета обеспечивает внешняя VPN/mesh-сеть — **NetBird** или аналог; собственного шлюза в проекте нет.

Плюс PWA на React, которая собирается внутрь образа Hub, и отдельное Android-приложение — форк Tempus (репозиторий `android/` в этот проект не входит).

Основные цифры:

- `hub/app` — около **28 000** строк Python;
- `hub/tests` — около **14 700** строк, **51** файл тестов;
- `embedder/app` — около **1 700** строк;
- `web/src` — около **16 000** строк TypeScript/TSX;
- **16** Alembic-миграций;
- точка входа Hub — `hub/app/main.py`, контракт API — `hub/API.md`.

---

## 1. Контекст системы

```text
┌──────────────────────┐
│ Android HomeMusic    │  форк Tempus + WebView с PWA
│ (Tempus-форк)        │
└──────────┬───────────┘
           │
           │ NetBird (или аналог)
           ▼
┌─────────────────────────────────────────────────────────────┐
│ Linux-хост, Docker Engine + Compose, docker compose          │
│                                                             │
│  navidrome:4533 ◄──► hub:8080                               │
│         ▲                │                                  │
│         │                ├──► embedder                       │
│         │                ├──► youtube-pot (PO tokens yt-dlp) │
│         │                ├──► slskd (опционально, Soulseek)  │
│         │                └──► внешние сервисы                │
│         │                                                   │
│  MUSIC_ROOT = /srv/music                                    │
│    library/ staging/ .trash/ .backups/                      │
└─────────────────────────────────────────────────────────────┘
```

### Внешние зависимости

| Сервис | Используется для |
|---|---|
| NetBird / VPN/mesh | доступ к Hub и Navidrome из интернета |
| Navidrome / Subsonic API | пользователи, звёзды, плейлисты, история скробблов, сканы, обложки |
| Deezer | каталог: исполнители, альбомы, треки, превью |
| hitmoz, hitmos.fm | русские треки, прямые файлы |
| YouTube / YouTube Music через yt-dlp | поиск и загрузка аудио |
| SoundCloud | fallback и re-upload’ы |
| Soulseek / slskd | опциональный lossless-источник для загрузок |
| Last.fm | похожие треки/исполнители, теги для названий миксов |
| Yandex Music | русские оригинальные названия, импорт коллекции, «Моя волна» Яндекса |
| MusicBrainz | MBID и метаданные |
| OpenRouter / любой OpenAI-совместимый LLM | ассистент, распознавание скриншотов, текстовые запросы |
| Google Identity | вход через Google |
| SMTP | регистрация по email |
| Hugging Face | загрузка весов MuQ-MuLan |
| NVIDIA GPU | инференс embedder |

---

## 2. Развёртывание и топология Docker Compose

Файл: `docker-compose.yml`.

| Сервис | Роль | Порты | Тома / особенности |
|---|---|---|---|
| `navidrome` | медиасервер | `4533:4533` | `navidrome-data`, `library` read-only, `.backups/navidrome` |
| `hub` | основной backend + PWA | `8080:8080` | `hub-data`, `MUSIC_ROOT`, `config.yaml` read-only, `soulseek-downloads` |
| `youtube-pot` | выдача PO-токенов для yt-dlp | только `expose: 4416` | изолирован, без пользовательских данных |
| `slskd` | Soulseek-демон | только `expose: 5030` | профиль `soulseek`, включается вручную |
| `embedder` | векторы звука | только `expose: 8090` | `embedder-data`, `embedder-models`, GPU reservation, `mem_limit: 4g` |

Важные архитектурные решения:

1. **SQLite лежит в именованных Docker-томах** на файловой системе Linux-хоста.
2. **Медиатека — каталог Linux-хоста** `/srv/music` (меняется через `MUSIC_ROOT`), примонтированный в контейнеры.
3. **Navidrome не использует watcher**: сканирование выполняет Hub (`startScan`) после импорта, плюс раз в час по расписанию.
4. **Navidrome и Hub публикуют порты** `4533` и `8080`; embedder, slskd и youtube-pot доступны только внутри compose-сети.
5. **Внешний доступ — вне проекта**: туннель, reverse proxy, DNS и TLS обеспечивает NetBird или аналогичная VPN/mesh-сеть.
6. **PWA собирается в образ Hub**: `web/` → `hub/app/static/`, оттуда FastAPI отдаёт SPA.

---

## 3. Hub: слоистая архитектура

```text
┌──────────────────────────────────────────────────────────────┐
│ Presentation                                                  │
│  FastAPI routers: app/api/*.py                                │
│  Pydantic-схемы: app/api/schemas.py                           │
│  SPA fallback и статика: app/main.py                          │
│  SSE: app/api/events.py                                       │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ Application / domain services                                 │
│  downloads, library, listen, plays, favorites, mixes,        │
│  wave, smart, imports, ai, insights, accounts, admin         │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ Infrastructure / adapters                                     │
│  providers/*, catalog/*, library/navidrome.py, net.py,       │
│  jobs/queue.py, db.py, models.py, config.py, security.py,    │
│  events.py, mailer.py, google_auth.py, housekeeping.py       │
└──────────────────────────────────────────────────────────────┘
```

### 3.1. Точка входа и композиция приложения

Файл: `hub/app/main.py`.

- `create_app()` создаёт `FastAPI`, подключает роутеры.
- `lifespan()` — composition root:
  - инициализирует настройки и логирование;
  - создаёт каталоги `library`, `staging`, `.trash`, `.backups`;
  - прогоняет Alembic-миграции;
  - инициализирует SQLAlchemy engine;
  - запускает `Services` — глобальные клиенты провайдеров и каталогов;
  - импортирует модули, регистрирующие job-хендлеры;
  - запускает `WorkerPool` и фоновые циклы;
  - при остановке отменяет задачи, останавливает пул, закрывает клиентов и БД.
- Отдаёт `/api/docs` и `/api/openapi.json`.
- Раздаёт собранный PWA из `hub/app/static`, с fallback на `index.html` для hash-роутера.

### 3.2. API-слой

| Роутер | Префикс | Ответственность |
|---|---|---|
| `api/auth.py` | `/api/auth` | вход, выход, `me`, регистрация по инвайту/email, Google |
| `api/admin.py` | `/api/admin` | пользователи, расходы ИИ, инвайты |
| `api/insights.py` | `/api/admin` | read-only аналитика: прослушивания, вкус, карта, миксы, волны |
| `api/search.py` | `/api/search` | история поиска, унифицированный поиск, провайдеры |
| `api/catalog.py` | `/api/catalog` | страницы артиста и альбома |
| `api/downloads.py` | `/api/downloads` | очередь загрузок, retry, cancel |
| `api/listen.py` | `/api/listen` | прослушивание до загрузки, prefetch, stream |
| `api/playlists.py` | `/api/playlists` | плейлисты Navidrome |
| `api/library.py` | `/api/library`, `/api/starred`, `/api/star` | собственная библиотека, редактор, звёзды |
| `api/uploads.py` | `/api/uploads` | загрузка своих файлов |
| `api/medialib.py` | `/api/nd` | прокси-представление библиотеки Navidrome |
| `api/plays.py` | `/api/plays`, `/api/dislikes` | события прослушивания, сводка, дизлайки |
| `api/mixes.py` | `/api/mixes`, `/api/taste` | «Микс дня», настройки, онбординг |
| `api/wave.py` | `/api/wave` | «Моя волна» |
| `api/smart_shuffle.py` | `/api/smart-shuffle` | умное перемешивание |
| `api/imports.py` | `/api/imports` | перенос коллекции из сервисов |
| `api/assistant.py` | `/api/assistant` | задачи распознавания и чат |
| `api/events.py` | `/api/events` | SSE-поток |
| `api/system.py` | `/healthz`, `/api/system`, `/share-target`, `/app/*` | здоровье, Soulseek, share target, Android-обновления |

### 3.3. Сервисный слой

#### `services.py` — глобальные синглтоны

`Services` создаёт и хранит:

- `providers: dict[str, Provider]` — hitmoz, hitmos_fm, ytdlp, yt_reupload, soundcloud, опционально soulseek;
- `deezer`, `hitmoz_catalog`, `musicbrainz`;
- `provider_order(query, for_download)` — выбирает порядок источников по языку запроса и режиму;
- `reupload_providers()` — источники re-upload’ов.

#### Основные доменные модули

| Компонент | Модули | Что делает |
|---|---|---|
| **Аккаунты** | `accounts.py`, `auth.py`, `security.py`, `mailer.py`, `google_auth.py`, `api/auth.py` | инвайты, email-коды, Google ID-token, сессии, Fernet-шифрование паролей |
| **Каталог** | `catalog/models.py`, `deezer.py`, `hitmoz_catalog.py`, `musicbrainz.py`, `yandex.py`, `cache.py` | единый `ref` вида `dz:…`, `hm:…`; TTL-кэш в `catalog_cache` |
| **Загрузки** | `jobs/downloads.py`, `providers/*`, `matching/*`, `library/*` | полный конвейер: каталог → дедупликация → поиск → выбор → скачивание → проверка → теги → импорт |
| **Библиотека** | `library/importer.py`, `tagger.py`, `layout.py`, `probe.py`, `editor.py`, `owned.py`, `navidrome.py` | раскладка файлов, теги, ffprobe/ffmpeg, поиск уже имеющегося, правки, Navidrome |
| **Прослушивание** | `listen.py`, `light.py`, `api/listen.py` | выбор файла, серверные копии, Range-стриминг, лёгкий Opus-вариант |
| **История** | `plays.py`, `listening_summary.py`, `favorites.py`, `api/plays.py` | события, скробблы Navidrome, дедупликация, звёзды, дизлайки |
| **Рекомендации** | `mixes/*`, `api/mixes.py`, `api/wave.py`, `api/smart_shuffle.py` | вкус, генераторы, отбор, порядок, волна, умный шаффл |
| **Импорт** | `imports/*`, `api/imports.py` | Yandex Music, Spotify export; snapshot → resolve → apply |
| **ИИ** | `ai/llm.py`, `extract.py`, `links.py`, `pipeline.py`, `agent.py`, `limits.py` | распознавание скриншотов/текста/ссылок, чат с инструментами |
| **Аналитика** | `insights/*` | read-only отчёты для администратора |
| **Обслуживание** | `housekeeping.py` | бэкапы, очистка, прогрев провайдеров |

### 3.4. Инфраструктурный слой

- `config.py` — двухуровневая конфигурация:
  - `Settings` из `.env` — секреты и пути;
  - `AppConfig` из `config.yaml` — источники, лимиты, веса, `matching`, `mixes`, `analytics`.
- `db.py` — async SQLAlchemy + SQLite WAL, `PRAGMA foreign_keys=ON`, `busy_timeout=10000`, миграции на старте.
- `models.py` — 33 таблицы.
- `net.py` — общие `httpx`-клиенты, rate limiter, retry/backoff, `Priority` для фоновых задач.
- `events.py` — in-process pub/sub для SSE.
- `security.py` — Fernet-шифрование секретов, хеширование токенов сессий.
- `logging_setup.py` — JSON-логи в stdout.
- `housekeeping.py` — ежедневные задачи и smoke-тест провайдеров.

---

## 4. Модель данных

Файл: `hub/app/models.py`, миграции: `hub/migrations/versions/`.

### Группы таблиц

| Группа | Таблицы | Назначение |
|---|---|---|
| Пользователи и доступ | `users`, `sessions`, `invites`, `email_codes` | аккаунты, сессии, приглашения |
| ИИ-учёт | `assistant_daily_usage`, `ai_usage` | лимиты и стоимость |
| Музыкальная библиотека | `albums`, `tracks`, `uploads` | собственные файлы и метаданные |
| Каталог | `catalog_cache` | TTL-кэш ответов Deezer/hitmoz/Last.fm и т. п. |
| Загрузки | `download_batches`, `download_items`, `pending_playlist_adds` | очередь и восстановление порядка плейлиста |
| Фоновые задачи | `jobs` | персистентная очередь |
| Ассистент | `assistant_tasks`, `assistant_chats` | задачи распознавания и сохранённые чаты |
| Импорт | `imports`, `import_collections`, `import_items`, `source_map` | перенос коллекций и общий кэш соответствий |
| История | `play_events`, `dislikes`, `favorite_subjects`, `navidrome_sync`, `search_history` | события, дизлайки, алиасы, синхронизация |
| Рекомендации | `mix_settings`, `mixes`, `mix_items`, `wave_sessions`, `wave_items`, `smart_sessions`, `smart_items`, `smart_hidden` | состояние и аналитика рекомендаций |

### Ключевые идентификаторы

- **`ref`** — каталожный идентификатор: `dz:<id>`, `hm:<id>`.
- **`nd_song_id`** — ID песни в Navidrome.
- **`uid`** — UUID, записываемый в теги `HOMEMUSIC_TRACK_ID` / `HOMEMUSIC_ALBUM_ID`; именно его Navidrome использует как стабильный PID.
- **`fingerprint`** — нормализованный `artist|title` для дедупликации.
- **`event_id`** — клиентский ID события; повторная отправка не создаёт дубль.
- **`mix_id` / `session_id`** — контекст рекомендаций.

### Важные инварианты

1. **Названия и ID не смешиваются без проверки**: `source_map` хранит подтверждённые соответствия внешних ID каталогу.
2. **Порядок плейлиста восстанавливается** через `pending_playlist_adds`, включая `needs_review` и рестарты.
3. **Скробблы Navidrome дедуплицируются** с собственными событиями приложения.
4. **Импортированные ♥ не считаются «своими»** для профиля вкуса.
5. **Миксы и волны не читают аналитические поля** `origin`, `client`, `restart_of` — это только телеметрия.

---

## 5. Фоновая обработка

### 5.1. Очередь задач

Файл: `hub/app/jobs/queue.py`.

- Очередь хранится в SQLite-таблице `jobs`.
- Атомарный claim через `UPDATE ... RETURNING`.
- При старте «зависшие» `running` возвращаются в `queued`.
- Повтор с экспоненциальной задержкой, `PermanentError` завершает сразу.
- Приоритеты:
  - `priority >= 0` — пользовательские задачи;
  - `PRIORITY_IMPORT = -10` — импорт и миксы;
  - фоновые задачи занимают не все воркеры: `background_slots() = workers - 1`.
- `notify()` будит воркеры после коммита.
- `WorkerPool` — набор asyncio-задач.

### 5.2. Зарегистрированные job-хендлеры

| Kind | Модуль |
|---|---|
| `download_item` | `jobs/downloads.py` |
| `expand_batch` | `jobs/downloads.py` |
| `nd_scan` | `jobs/downloads.py` |
| `mix_build`, `mix_sync` | `mixes/core.py` |
| `import_auth`, `import_fetch`, `import_resolve`, `import_playlists`, `import_finish` | `imports/core.py` |
| `assistant_task` | `ai/pipeline.py` |

### 5.3. Фоновые циклы

| Цикл | Модуль | Частота |
|---|---|---|
| `scheduler_loop` | `housekeeping.py` | раз в сутки, в `backup_hour` |
| `scrobbles_loop` | `plays.py` | первый запуск через 2 минуты, далее раз в час |
| `schedule_loop` | `mixes/core.py` | проверка пользователей, которым пора собрать микс |
| `playlist_adds_loop` | `jobs/downloads.py` | каждые 30 секунд |
| SSE keepalive | `api/events.py` | `ping` каждые 20 секунд |

---

## 6. Ключевые сквозные потоки

### 6.1. Аутентификация

```text
Login form
  → POST /api/auth/login
  → NavidromeClient.ping() + get_user()
  → User upsert (пароль шифруется Fernet)
  → Session (sha256 токена)
  → HttpOnly cookie hm_session
```

Дополнительно:

- инвайты — `invites`;
- email-регистрация — `email_codes` + SMTP;
- Google — проверка RS256 ID-token по публичным ключам Google;
- любой запрос, кроме `/healthz` и логина, требует cookie;
- `current_user()` освобождает транзакцию аутентификации до входа в долгий endpoint.

### 6.2. Поиск и каталог

```text
GET /api/search
  → библиотека (локальные tracks)
  → Deezer (основной каталог)
  → fallback hitmoz
  → ownership annotation
```

Для «Файлов»:

```text
GET /api/search/providers
  → hitmoz / hitmos_fm / ytdlp / yt_reupload / soundcloud
  → опционально soulseek
  → Candidate + file_ref: "file:<provider>:<id>"
```

### 6.3. Загрузка трека

```text
POST /api/downloads
  → DownloadBatch + DownloadItem(status=queued)
  → job expand_batch (для альбомов/артистов)
  → job download_item
      1. resolve_catalog_meta
      2. find_existing — уже в библиотеке?
      3. russian_original — кириллический оригинал
      4. Soulseek (если включён)
      5. файл, который пользователь только что слушал
      6. provider_order: поиск → rank → fetch
      7. postcheck: ffprobe, длительность, тишина
      8. tagger + importer → staging → library
      9. Track row
     10. job nd_scan
          → Navidrome startScan
          → link nd_song_id
          → завершение download item
          → добавление в плейлист
          → mix/import hooks
```

Особые случаи:

- явный русский трек ищет «без цензуры»;
- выбранный re-upload может заменить цензурированную версию как `[v2]`;
- без каталожной записи элемент уходит в `needs_review`;
- `needs_review` не блокирует более поздние готовые песни;
- «тихие» загрузки (`star`, `import`, `mix`, `wave`) не показываются пользователю как обычные.

### 6.4. Прослушивание до загрузки

```text
GET /api/listen?ref=…
  → выбрать файл так же, как для загрузки
  → запомнить pick в catalog_cache на 30 дней

GET /api/listen/stream?ref=…
  → library file, server copy, preview или light-вариант
  → Range → 206
```

- Серверные копии лежат в `staging/listen`.
- Для источников без plain HTTP audio (SoundCloud/HLS) сначала скачивается копия.
- `q=mobile` → Ogg Opus 160 kbps, копии в `staging/listen-light`.
- `X-HomeMusic-Listen-Kind` и `X-HomeMusic-Variant` сообщают клиенту, что именно отдано.

### 6.5. История и вкус

```text
POST /api/plays/events
  → PlayEvent (start/skip/finish/like/unlike/dislike/undislike)
  → дедупликация по event_id
  → заполнение ref ↔ nd_song_id
  → скробблы Navidrome импортируются раз в час
```

Профиль вкуса:

- `mixes/taste.py` — v1;
- `mixes/taste_math.py` — v2;
- `mixes/taste_snapshot.py` — явный snapshot входных данных;
- `mixes/taste_select.py` — геометрия и выбор seeds.

### 6.6. «Микс дня»

```text
schedule_loop
  → due_users()
  → enqueue mix_build
  → build_for(user)
      → profile = build_profile(user)
      → generators: favorites, stash, ytm_radio,
        lastfm_similar, deezer_radio, yandex_similar,
        similar_artists, new_releases, yandex_wave
      → merge → keep → score
      → sound term через embedder
      → select_songs (50 + reserve)
      → order
      → name_mix
      → save Mix + MixItem
      → quiet downloads
      → Navidrome playlist «Микс дня»
  → mix_sync после завершения загрузок
      → замена неудачных песен резервом
      → обновление плейлиста
      → очистка старых ненужных файлов
```

Ключевые модули:

- `mixes/build.py` — генераторы, фильтры, скор, порядок, название;
- `mixes/core.py` — жизненный цикл микса, плейлист, загрузки, очистка;
- `mixes/resolve.py` — сопоставление «исполнитель — название» с каталогом;
- `mixes/sources.py` — Last.fm, YouTube Music radio, Yandex;
- `mixes/embedder.py` — клиент embedder.

### 6.7. «Моя волна»

Файл: `mixes/wave.py`.

- Сессия хранит пул кандидатов, выданные треки, последние 50 проигранных, скипы и лайки.
- `start` создаёт сессию:
  - без seed — от профиля вкуса;
  - по треку;
  - по исполнителю;
  - по артисту Navidrome.
- `next` принимает события с последнего вызова и выдаёт до 5 треков.
- Скип < 30 секунд или дизлайк понижают исполнителя и похожие по звуку треки.
- ♥ или дослушанный трек добавляют радио YouTube Music в пул.
- Исполнитель играет не чаще одного раза в 8 треков.
- `WaveItem` фиксирует выдачу для аналитики.

### 6.8. «Умное перемешивание»

Файл: `mixes/smart.py`, чистые правила — `mixes/smart_order.py`.

- Контекст: `liked` или `playlist:<id>`.
- Свои песни перемешиваются алгоритмом «fewer repeats».
- Рекомендации вставляются после каждых 2–3 своих песен.
- Кандидаты берутся из генераторов микса.
- Учитываются звуковая близость и поведение в сессии.
- `smart_hidden` — «не рекомендовать в этом плейлисте», не глобальный дизлайк.
- Плейлист никогда не меняется самим шаффлом.

### 6.9. Импорт коллекции

Модули: `imports/sources/*`, `imports/resolve.py`, `imports/apply.py`, `imports/core.py`.

```text
источник (Yandex / Spotify export)
  → Snapshot
  → ImportCollection + ImportItem
  → import_resolve: owned / exact / probable / not_found
  → apply:
      ♥ в порядке сервиса
      плейлисты Navidrome
      загрузки с низким приоритетом
  → import_finish
```

`source_map` общий для всех пользователей: повторный импорт тех же песен почти не ищет каталог.

### 6.10. ИИ-ассистент

Два режима:

1. **Задача распознавания** (`ai/pipeline.py`):
   - скриншоты → vision-модель;
   - ссылка → точные данные без LLM;
   - текст → LLM или быстрый путь «Artist - Title»;
   - результат → карточка подтверждения;
   - только после `confirm_task` создаются загрузки и плейлисты.

2. **Свободный чат** (`ai/agent.py`):
   - LLM с инструментами;
   - чтение аккаунта, библиотеки, плейлистов, импортов, загрузок;
   - изменения ♥, плейлистов, загрузок;
   - удаление плейлиста или файлов — только через proposal + подтверждение.

Лимиты: `assistant_daily_usage`, `ai_usage`, `ai/limits.py`.

### 6.11. Аналитика администратора

Модули: `insights/*`, `api/insights.py`.

Принципы:

- только чтение;
- без сети, кроме embedder `/v2/profile` и `/v2/project`;
- `Cache-Control: no-store`;
- те же расчёты вкуса, что и в продакшене;
- исключение тестовых пользователей через `analytics.exclude_users`.

---

## 7. Embedder: архитектура сервиса

```text
┌─────────────────────────────────────────────────────┐
│ embedder (FastAPI)                                  │
│                                                     │
│  /healthz                                           │
│  /embed, /embed/{key}       — очередь эмбеддингов   │
│  /similarity, /vectors, /text                       │
│  /v2/profile, /v2/similarity, /v2/vectors,          │
│  /v2/shadow, /v2/project                            │
│                                                     │
│  ┌────────────┐   ┌────────────┐   ┌─────────────┐ │
│  │ Store      │   │ Worker     │   │ Geometry    │ │
│  │ SQLite     │◄──│ очередь    │──►│ snapshot,   │ │
│  │ items      │   │ + Engine   │   │ PCA, facets │ │
│  └────────────┘   └─────┬──────┘   └─────────────┘ │
│                         │                           │
│                  ┌──────▼──────┐                    │
│                  │ Model       │                    │
│                  │ MuQ-MuLan / │                    │
│                  │ FakeModel   │                    │
│                  └─────────────┘                    │
└─────────────────────────────────────────────────────┘
```

### Компоненты

| Модуль | Роль |
|---|---|
| `app/main.py` | HTTP API, auth по токену, healthz, v2-геометрия |
| `app/config.py` | настройки из env |
| `app/db.py` | SQLite-очередь и векторы |
| `app/audio.py` | скачивание URL, путь в библиотеке, decode через ffmpeg |
| `app/models.py` | `Model` protocol, `MuQ-MuLan`, `FakeModel`, выбор GPU |
| `app/worker.py` | `Engine` — ленивая загрузка модели, выгрузка по idle, очередь |
| `app/search.py` | косинусы, центры, top-N |
| `app/geometry.py` | центрированная геометрия v2, PCA, facets |
| `app/bench.py` | замер скорости |

### Ключевые особенности

- Модель загружается **по требованию** и выгружается после `idle_unload_s`.
- GPU общая: если свободно меньше `min_free_gpu_mb`, выбрасывается `GpuBusy`, воркер повторяет позже.
- Векторы хранятся в SQLite BLOB.
- `space_id` защищает от смешивания координат разных snapshot’ов.
- v2-геометрия центрирует векторы и строит facets; Hub использует это для миксов, волны и карты вкуса.
- Тесты используют `FakeModel`; реальная модель — отдельный маркер `gpu`.

---

## 8. PWA: архитектура фронтенда

### 8.1. Слои

```text
web/src/
  main.tsx           — монтирование, service worker, update watcher
  App.tsx            — загрузка сессии, роутинг, layout, провайдеры
  api/
    client.ts        — fetch, cache, ошибки, upload, stream URL
    types.ts         — контракт API
    insights.ts      — admin insights API
  lib/               — состояние и поведение
  components/        — переиспользуемые UI-блоки
  pages/             — экраны
  public/            — manifest, иконки, service worker
  mock/              — Vite mock API
```

### 8.2. Роутинг

- Хэш-роутер собственной реализации: `lib/router.tsx`.
- `App.tsx` содержит таблицу маршрутов.
- `STANDALONE` и `EMBEDDED`:
  - в браузере доступны все экраны;
  - внутри Android WebView — только разделы Хаба, навигация нативная.
- Неизвестный маршрут ведёт на `/home` или `/search`.

### 8.3. Состояние и поведение

| Модуль | Роль |
|---|---|
| `lib/session.ts` | контекст текущего пользователя |
| `lib/listen.ts` | собственный браузерный плеер, очередь, отчёты о прослушивании |
| `lib/player.ts` | мост в Android-плеер через `HomeMusicAndroid` |
| `lib/wave.ts` | клиент «Моей волны» |
| `lib/smartShuffle.ts` | клиент умного шаффла |
| `lib/plays.ts` | отправка событий и launch-контекст |
| `lib/stars.ts` | типизированные ♥ |
| `lib/dislikes.ts` | дизлайки |
| `lib/events.ts` | единый SSE-клиент |
| `lib/actions.ts` | запуск загрузок |
| `lib/notify.ts` | уведомления и бейдж загрузок |
| `lib/capabilities.ts` | проверка `/healthz` на возможности сервера |
| `lib/update.ts` | перезагрузка при новом `web_build` |
| `lib/shareStore.ts` | чтение Share Target из IndexedDB |
| `lib/desktop.ts` | детект десктопа |
| `lib/waveAudio.ts`, `lib/waveGlow.ts` | визуализация волны |

### 8.4. Компоненты

- `TabBar`, `MiniPlayer`, `PlayerSpacer`;
- `Overlays` — тосты и диалоги;
- `TrackMenu`, `LikeButton`, `candidate`;
- `catalog`, `pickers`, `ui`;
- `Wave`, `WaveHero`, `WaveGlow`;
- `charts/*` — BarsByDay, Funnel, Heatmap, ScatterMap, ShareBar, Weight.

### 8.5. Service worker

Файл: `web/public/sw.js`.

- Кэширует app shell и hashed assets.
- Никогда не кэширует `/api/*`.
- Обрабатывает `POST /share-target`.
- Хранит входящие файлы в IndexedDB.
- Обрабатывает клики по уведомлениям.
- BUILD_ID вставляется Vite при сборке.

### 8.6. Интеграция с Android

Через `window.HomeMusicAndroid`:

- `play(songIdsJson, index)`;
- `playQueue(itemsJson, index)`;
- `playWave(waveJson)`;
- `pauseNative()`;
- `webPlayer(visible)`;
- `shareText(...)`.

Правило: одновременно играет только один плеер — либо нативный, либо страничный.

---

## 9. Внешний доступ

В проекте **нет** собственного шлюза, обратного SSH-туннеля, reverse proxy или скриптов настройки VPS.
Компонент `gateway/` удалён. Доступ из интернета обеспечивает **NetBird** или аналогичная VPN/mesh-сеть:

- Hub и Navidrome слушают локальные порты `8080` и `4533`;
- адреса клиентов задаются в `.env`: `PUBLIC_HUB_URL` и `PUBLIC_MUSIC_URL`;
- эти адреса используются для приглашений, QR-кодов и кнопки «Navidrome»;
- DNS, TLS, маршрутизация и проверка доступа — на стороне выбранного VPN/mesh-решения.

Проверка после настройки: клиент VPN подключён, `http://<hub-host>:8080/healthz` отвечает,
Navidrome доступен по `http://<navidrome-host>:4533`.

---

## 10. Тестирование и инструменты

### Hub

- `pytest`, `pytest-asyncio`, `pytest-xdist`, `respx`.
- `hub/tests/conftest.py`:
  - один раз прогоняет Alembic-миграции в шаблон;
  - каждый тест получает копию БД;
  - поднимает приложение in-process через `httpx.ASGITransport`;
  - отключает фоновые задачи;
  - подменяет Navidrome, LLM, SMTP, embedder, Last.fm.
- `hub/tests/support.py` — `FakeProvider`, фейковый Navidrome и помощники.
- Отдельный маркер `network` для тестов с реальной сетью.
- `ruff` — линтер.

### Embedder

- `FakeModel` для детерминированных тестов.
- `tests/test_real_model.py` — отдельный прогон с настоящей моделью.
- `app/bench.py` — замер на файлах библиотеки.

### Eval

- `eval/run_eval.py` — precision/recall распознавания скриншотов.
- `eval/expected.example.json` — формат ожиданий.
- Требование: recall ≥ 0.90.

### Release

- `scripts/release-android.ps1` — сборка Android APK, bsdiff-патчи, проверка подписи и хэшей, публикация через Hub.
  Скрипт выпуска Android пока использует Windows/PowerShell-пути; это не относится к Linux-хосту сервера.

---

## 11. Архитектурные инварианты и сквозные решения

1. **Navidrome — источник истины для пользователей, звёзд и плейлистов.**
   Hub хранит зашифрованный пароль и действует от имени пользователя через Subsonic API.

2. **Стабильные ID важнее путей.**
   `HOMEMUSIC_TRACK_ID` и `HOMEMUSIC_ALBUM_ID` записываются в теги, поэтому перемещение и перетегирование не ломают плейлисты и историю.

3. **Двухуровневая конфигурация.**
   Секреты — только `.env`; поведение — `config.yaml`. `config.yaml` монтируется read-only.

4. **Фоновая работа не должна мешать пользователю.**
   `Priority` в `net.py` и отрицательные приоритеты задач отдают сеть и воркеры пользовательским действиям.

5. **Все внешние ответы кэшируются.**
   `catalog_cache` с TTL; пики прослушивания — до 30 дней; `source_map` — бессрочно.

6. **Идемпотентность.**
   `event_id`, уникальные ключи, `pending_playlist_adds`, повторная отправка батчей, восстановление после рестарта.

7. **Единый контракт API.**
   `app/api/schemas.py` — pydantic-модели; `hub/API.md` — документация; PWA и Android используют одни и те же endpoint’ы.

8. **Безопасность.**
   HttpOnly cookie, Fernet для секретов, SHA-256 для токенов сессий, ограничение попыток входа, изоляция embedder/slskd/youtube-pot.

9. **Аналитика не влияет на рекомендации.**
   Поля `launch_id`, `origin`, `client` читаются только `insights/*`.

---

## 12. Риски и ограничения архитектуры

1. **Один процесс Hub.**
   `EventBus` и часть runtime-состояния (`wave._rt`, `smart`-сессии) живут в памяти процесса. Запуск uvicorn с несколькими workers сломает SSE и часть состояния. Это осознанное решение для домашнего сервиса.

2. **SQLite как единственная БД.**
   WAL и `busy_timeout` помогают, но при росте нагрузки или нескольких писателях возможны блокировки. Бэкапы выполняются через `sqlite3.backup()` в Docker-том.

3. **Тесная связка с Navidrome.**
   Многие функции (звёзды, плейлисты, сканы, история) зависят от Subsonic API и поведения Navidrome.

4. **Bind mount медиатеки и права доступа.**
   Каталоги `/srv/music` должны быть доступны контейнерам на чтение и запись; файлы, созданные Hub’ом,
   могут принадлежать root. Это стоит учитывать при ручной правке библиотеки.

5. **GPU разделяется с другими задачами.**
   Embedder может не загрузить модель при нехватке памяти; Hub должен продолжать работать без звукового признака.

6. **Внешние источники нестабильны.**
   Верстка hitmoz/hitmos.fm, поведение YouTube, SoundCloud и Soulseek могут меняться. Есть smoke-тест провайдеров и `needs_review`.

7. **Документация частично внешняя.**
   README ссылается на `PLAN.md`, `MIXES.md`, `INSIGHTS.md`, которых нет в репозитории. `hub/API.md` — самый полный внутренний контракт.

8. **Android-приложение вне репозитория.**
   `android/` — отдельный git-репозиторий; релизный скрипт пока зависит от Windows-путей (`B:\Music`, `B:\AndroidEmu`) и не входит в Linux-развёртывание сервера.

9. **Внешний доступ не входит в проект.**
   Нет встроенного шлюза и проверки состояния NetBird/VPN: если доступ снаружи не работает, это диагностируется
   средствами выбранной mesh-сети, а не Hub’а.

---

## 13. Точки расширения

| Что добавить | Куда |
|---|---|
| Новый источник загрузок | реализовать `Provider` из `hub/app/providers/base.py`, зарегистрировать в `hub/app/services.py`, добавить в `config.yaml → sources` |
| Новый каталог | модуль в `hub/app/catalog/`, свой `ref`-префикс, клиент в `Services`, маршруты в `api/catalog.py` |
| Новый источник импорта | модуль в `hub/app/imports/sources/`, возвращающий `Snapshot` |
| Новый генератор микса | функция в `hub/app/mixes/build.py`, вес в `MixesCfg.weights`, название в `GENERATOR_TITLES` |
| Новая модель эмбеддингов | реализовать `Model` protocol в `embedder/app/models.py` и выбрать её в `load_model` |
| Новая страница PWA | компонент в `web/src/pages/`, маршрут в `web/src/App.tsx` |
| Новая фоновая задача | `@handler("kind")` + `enqueue()` в транзакции |
| Новый тип события SSE | `EventBus.publish()` + обработчик в `web/src/lib/events.ts` |

---

## 14. Итоговая схема компонентов

```text
                         ┌─────────────────────────┐
                         │        Android          │
                         │  Tempus + WebView PWA   │
                         └───────────┬─────────────┘
                                     │
                          NetBird / аналог (VPN/mesh)
                                     │
        ┌────────────────────────────▼────────────────────────────┐
        │                    Docker Compose                       │
        │                                                        │
        │  ┌────────────┐      ┌─────────────────────────────┐   │
        │  │ Navidrome  │◄────►│            Hub              │   │
        │  │ Subsonic   │      │                             │   │
        │  └────────────┘      │  API routers                │   │
        │        ▲             │  Auth / Accounts            │   │
        │        │             │  Catalog                    │   │
        │        │             │  Providers + Matching       │   │
        │        │             │  Downloads + Job queue      │   │
        │        │             │  Library + Tagger           │   │
        │        │             │  Listen + Light             │   │
        │        │             │  Plays + Favorites          │   │
        │        │             │  Mixes / Wave / Smart       │   │
        │        │             │  Imports                   │   │
        │        │             │  AI Assistant              │   │
        │        │             │  Insights                  │   │
        │        │             │  SSE EventBus              │   │
        │        │             └──────┬──────────┬──────────┘   │
        │        │                    │          │              │
        │        │             ┌──────▼───┐  ┌───▼──────────┐   │
        │        │             │ Embedder │  │ youtube-pot  │   │
        │        │             │ MuQ-MuLan│  │ yt-dlp PO    │   │
        │        │             └──────────┘  └──────────────┘   │
        │        │                                               │
        │        └────────────── slskd (опционально) ────────────┘
        │                                                        │
        │  MUSIC_ROOT: library/ staging/ .trash/ .backups/       │
        └────────────────────────────────────────────────────────┘
```

Ключевая идея архитектуры: **Hub — это оркестратор**, который соединяет внешние источники музыки, каталог, Navidrome, локальную файловую библиотеку, историю прослушивания и рекомендательную систему. Navidrome отвечает за воспроизведение и пользовательские данные, embedder — за звуковую близость, а PWA/Android — за интерфейс.
