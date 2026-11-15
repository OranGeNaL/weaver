## Purpose

Домен описывает системные контракты HomeMusic: health и capabilities, SSE-события, Share Target, обновления Android-приложения, административную аналитику, housekeeping и раздачу PWA. Признак внешнего медиасервера в health отсутствует. iOS сейчас не поддерживается; клиентские возможности определяются через `/healthz`.

## Requirements

### Requirement: Публичный health и capabilities

Hub SHALL отдавать `GET /healthz` без авторизации с признаком доступности Navidrome, версией, идентификатором сборки PWA и списком capabilities. Capabilities MUST позволять клиентам выбирать поддерживаемые функции без угадывания версии Hub.

#### Scenario: Hub доступен
- **WHEN** клиент вызывает `/healthz`
- **THEN** Hub возвращает `ok: true`
- **AND** отдельно сообщает `navidrome: true|false`
- **AND** возвращает `web_build` и список capabilities

#### Scenario: Navidrome недоступен
- **WHEN** Navidrome не отвечает
- **THEN** `/healthz` всё равно возвращает `ok: true`
- **AND** `navidrome` равен `false`

#### Scenario: Новый клиент
- **WHEN** клиент видит `typed_favorites_v1`, `listening_checkpoints_v1`, `launch_events_v1`, `wave_origin_v1` или `smart_shuffle_v1`
- **THEN** он MAY использовать соответствующее расширение API
- **AND** старый клиент продолжает работать без него

### Requirement: Capability избранного Hub

Hub SHALL сообщать в `/healthz.capabilities` возможность `hub_liked_v1`, означающую, что `/api/starred` и `/api/library/liked` обслуживаются Hub и содержат `track_uids`.

#### Scenario: Новый клиент
- **WHEN** клиент видит `hub_liked_v1`
- **THEN** он MAY использовать `track_uids` и `/api/library/liked`
- **AND** старый клиент продолжает использовать `song_ids`

#### Scenario: Старый клиент
- **WHEN** клиент не знает `hub_liked_v1`
- **THEN** он работает с прежними полями `/api/starred`

### Requirement: Capability каталога Hub

Hub SHALL сообщать в `/healthz.capabilities` возможность `hub_library_v1`, означающую, что каталог, обложки и библиотечное прослушивание доступны через `/api/library/*` и `/api/listen?ref=lib:<uid>`.

#### Scenario: Новый клиент
- **WHEN** клиент видит `hub_library_v1`
- **THEN** он MAY использовать `/api/library/*` и `lib:<uid>`
- **AND** не обращается к `/api/nd/*`

#### Scenario: Старый клиент
- **WHEN** клиент не знает `hub_library_v1`
- **THEN** он продолжает работать через прежние endpoint'ы до этапа 7

### Requirement: Административная диагностика

Hub SHALL отдавать администратору расширенный health с проверкой провайдеров и статусом Soulseek. Эти endpoint'ы MUST быть доступны только администратору.

#### Scenario: Проверка провайдеров
- **WHEN** администратор вызывает `GET /api/system/health`
- **THEN** Hub выполняет smoke-поиск по провайдерам
- **AND** возвращает результат по каждому источнику

#### Scenario: Статус Soulseek
- **WHEN** администратор вызывает `GET /api/system/soulseek`
- **THEN** Hub возвращает `disabled`, `unreachable`, `auth_error`, `connecting`, `disconnected` или `ready`

#### Scenario: Тестовый поиск Soulseek
- **WHEN** администратор передаёт `search=true` и Soulseek готов
- **THEN** Hub выполняет пробный поиск и возвращает число файлов или ошибку

#### Scenario: Неадминистратор
- **WHEN** обычный пользователь вызывает диагностический endpoint
- **THEN** Hub возвращает `403`

### Requirement: Server-Sent Events

Hub SHALL отдавать `GET /api/events` как `text/event-stream` с именованными событиями и JSON в `data`. Каждое событие MUST получать только его пользователь; при отсутствии событий Hub MUST отправлять `ping` каждые 20 секунд.

#### Scenario: Подключение к SSE
- **WHEN** авторизованный пользователь открывает `/api/events`
- **THEN** Hub создаёт очередь событий этого пользователя
- **AND** сразу отправляет `ping`

#### Scenario: Прогресс загрузки
- **WHEN** статус или прогресс загрузки меняется
- **THEN** Hub публикует событие `download` с `DownloadItemOut`
- **AND** только этому пользователю

#### Scenario: Статус ассистента
- **WHEN** задача ассистента меняет статус
- **THEN** Hub публикует `assistant` с `{id, status}`

#### Scenario: Изменение библиотеки
- **WHEN** библиотека изменена, импортирована или у трека снят ♥
- **THEN** Hub публикует `library` с типом изменения
- **AND** идентификаторы (`album_id`, `track_id`, `song_ids`) передаются только там, где они есть у публикатора; `starred`/`unstarred` могут быть без `song_ids`

#### Scenario: Медленный клиент
- **WHEN** очередь SSE заполнена
- **THEN** Hub отбрасывает новые сообщения для этого клиента
- **AND** не блокирует остальных подписчиков

### Requirement: Share Target

Hub SHALL принимать `POST /share-target` как fallback для Web Share Target. Без cookie `hm_session` запрос MUST перенаправляться на страницу входа; при наличии cookie — на страницу Share PWA, даже если сессия просрочена или поддельна.

#### Scenario: Нет cookie
- **WHEN** запрос приходит без cookie `hm_session`
- **THEN** Hub отвечает `303` на `/#/login?next=share`

#### Scenario: Cookie присутствует
- **WHEN** запрос приходит с любой cookie `hm_session`
- **THEN** Hub отвечает `303` на `/#/share?fallback=1`
- **AND** содержимое обрабатывает PWA через service worker

### Requirement: Обновления Android-приложения

Hub SHALL раздавать манифест обновлений, полный APK и неизменяемые артефакты по адресам `/app/latest.json`, `/app/latest.apk`, `/app/artifacts/*`. Манифест MUST проверять размеры и SHA-256, а патчи MUST предлагаться только для корректной базовой версии.

#### Scenario: Манифест обновления
- **WHEN** клиент вызывает `/app/latest.json`
- **THEN** Hub возвращает `versionCode`, `versionName`, `notes`, `size`, `sha256` и `url`
- **AND** при наличии валидного `full` добавляет `full` и `patches`

#### Scenario: Полный APK
- **WHEN** клиент вызывает `/app/latest.apk`
- **THEN** Hub отдаёт текущий APK как `application/vnd.android.package-archive`

#### Scenario: Патч
- **WHEN** клиент скачивает `/app/artifacts/patch-<sha256>.bsdiff`
- **THEN** Hub отдаёт файл с `Cache-Control: immutable`
- **AND** проверяет, что имя, размер и SHA-256 соответствуют артефакту

#### Scenario: Нет сборки
- **WHEN** `latest.json` отсутствует или повреждён
- **THEN** Hub возвращает `404` «Нет сборки приложения»

### Requirement: Административная аналитика

Hub SHALL предоставлять администратору read-only аналитику прослушивания, вкуса, миксов, волн и матрицы пользователей. Все ответы MUST иметь `Cache-Control: no-store`; запросы MUST NOT изменять данные и MUST NOT ходить в сеть, кроме embedder facets.

#### Scenario: Обзор
- **WHEN** администратор вызывает `/api/admin/insights/overview`
- **THEN** Hub возвращает агрегаты по пользователям за период
- **AND** исключает пользователей из `analytics.exclude_users`, если не передан `include_excluded=true`

#### Scenario: Вкус пользователя
- **WHEN** администратор открывает taste-отчёт
- **THEN** Hub использует те же расчёты, что и рекомендации
- **AND** возвращает вклад событий и ограничения модели

#### Scenario: Карта вкуса
- **WHEN** администратор запрашивает карту
- **THEN** Hub возвращает точки в звуковом пространстве или `available: false` с причиной, если embedder недоступен

#### Scenario: Неверный период
- **WHEN** даты периода перевёрнуты, слишком длинные или некорректны
- **THEN** Hub возвращает `422`

#### Scenario: Неизвестный пользователь
- **WHEN** запрошен несуществующий пользователь, микс, волна или трек
- **THEN** Hub возвращает `404`

### Requirement: Housekeeping

Hub SHALL ежедневно выполнять обслуживание: логическую резервную копию БД Hub через `pg_dump`, очистку корзины, устаревших `staging/uploads`, истёкшего `catalog_cache`, старых email-кодов, GC осиротевших блобов и вытеснение hot-cache, а также smoke-тест провайдеров. Ежедневная очистка не обходит `staging/dl/*`; копии прослушивания живут в hot-cache и вытесняются по LRU. Бэкап и smoke-тест изолированы, а очистки выполняются одной группой: ошибка в очистке прекращает оставшиеся очистки, но не мешает smoke-тесту.

#### Scenario: Ежедневный запуск
- **WHEN** наступает `library.backup_hour` по времени контейнера (обычно UTC)
- **THEN** Hub выполняет ежедневные задачи
- **AND** пишет результат в лог

#### Scenario: Очистка корзины
- **WHEN** у трека `deleted_at` старше `trash_days`
- **THEN** Hub удаляет строку трека
- **AND** затем GC удаляет блоб, если ссылок не осталось

#### Scenario: Очистка uploads и кэша
- **WHEN** файлы uploads старше установленного срока или записи `catalog_cache` истекли
- **THEN** Hub удаляет их

#### Scenario: Копии прослушивания
- **WHEN** наступает ежедневная очистка
- **THEN** `staging/dl/*` не очищается housekeeping'ом
- **AND** копии прослушивания живут в hot-cache и вытесняются по LRU

#### Scenario: Сбой очистки
- **WHEN** одна из очисток (trash/GC, uploads, cache, email) завершилась ошибкой
- **THEN** Hub логирует ошибку
- **AND** оставшиеся очистки в этой группе не выполняются
- **AND** smoke-тест провайдеров всё равно запускается

#### Scenario: Бэкап БД
- **WHEN** наступает время бэкапа
- **THEN** Hub создаёт `pg_dump`-дамп Postgres
- **AND** сохраняет несколько последних копий
- **AND** не удаляет предыдущие копии при ошибке дампа

#### Scenario: GC блобов
- **WHEN** блоб не имеет ссылок дольше `trash_days` или объект в бакете отсутствует в БД дольше TTL
- **THEN** housekeeping удаляет объект из S3
- **AND** не трогает блобы, на которые есть ссылки

#### Scenario: Вытеснение hot-cache
- **WHEN** размер локального кэша превышает лимит
- **THEN** housekeeping удаляет записи, которые дольше всего не использовались

### Requirement: Раздача PWA

Hub SHALL раздавать собранную PWA из `hub/app/static` и MUST отдавать `index.html` для клиентских маршрутов. Запросы к несуществующим `/api/*` MUST возвращать JSON `404`, а не HTML.

#### Scenario: Корневой маршрут
- **WHEN** клиент открывает `/`
- **THEN** Hub отдаёт `index.html`

#### Scenario: Клиентский маршрут
- **WHEN** клиент открывает неизвестный путь без `/api/`
- **THEN** Hub отдаёт `index.html` для hash-роутера

#### Scenario: Неизвестный API
- **WHEN** клиент открывает несуществующий `/api/...`
- **THEN** Hub возвращает JSON `404`

#### Scenario: Service worker
- **WHEN** клиент запрашивает `sw.js` или `manifest.webmanifest`
- **THEN** Hub отдаёт файл с `Cache-Control: no-cache`

### Requirement: Клиентские режимы и мост Android

Hub SHALL поддерживать PWA в браузере и PWA внутри Android WebView. Embedded-режим MUST определяться наличием параметра `embedded` (в том числе `embedded=0`), объектом `HomeMusicAndroid` или User-Agent `HomeMusicAndroid/<version>`; iOS сейчас не поддерживается.

#### Scenario: Обычный браузер
- **WHEN** PWA открыта без Android-моста
- **THEN** доступны экраны standalone-сервиса и страничный плеер

#### Scenario: Android WebView
- **WHEN** PWA открыта внутри приложения
- **THEN** навигация остаётся нативной, а PWA показывает только разделы Хаба

#### Scenario: Нативный плеер
- **WHEN** доступен `HomeMusicAndroid.playQueue` или `playWave`
- **THEN** PWA передаёт очередь или волну нативному плееру
- **AND** страничный плеер не играет одновременно

#### Scenario: iOS
- **WHEN** клиент не является PWA, Android WebView или Android-приложением
- **THEN** Hub не обещает отдельный iOS-контракт

### Requirement: Health хранилища

Hub SHALL сообщать состояние S3-хранилища в публичном и административном health: доступность бакета и занятость локального кэша. Деградация хранилища MUST NOT делать `/healthz` недоступным.

#### Scenario: S3 доступен
- **WHEN** клиент вызывает `/healthz`, а бакет доступен
- **THEN** Hub возвращает `ok: true`
- **AND** сообщает статус хранилища как доступный

#### Scenario: S3 недоступен
- **WHEN** бакет недоступен
- **THEN** `/healthz` всё равно возвращает `ok: true`
- **AND** статус хранилища равен `down` или `degraded`

#### Scenario: Административная диагностика
- **WHEN** администратор запрашивает расширенный health
- **THEN** Hub возвращает объём библиотеки и занятость кэша
- **AND** не выполняет тяжёлых операций с бакетом
