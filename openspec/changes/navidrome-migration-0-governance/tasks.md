## 1. Контекст и стек

- [x] 1.1 Обновить `openspec/config.yaml → context`: Postgres вместо SQLite, S3 primary + локальный hot-cache, PWA как основной клиент, Navidrome выведен, рабочих инстансов нет; проверить `rg -in "subsonic|sqlite" openspec/config.yaml` — **0 совпадений**, а `rg -in "navidrome|MUSIC_ROOT"` — только намеренные строки: `MUSIC_ROOT` в staging-строке и `Navidrome не используется` в design.rules; проверить после 2.4.
- [x] 1.2 Обновить раздел «Домены Openspec»: зафиксировать `persistence` (Postgres, Alembic, бэкапы), уточнить владельцев `storage` / `library` / `listening` / `auth` / `recommendations` (векторы); проверить, что у каждого домена один владелец данных.
- [x] 1.3 Обновить раздел «Тестирование»: тесты Hub работают на Postgres, а не на шаблонной SQLite-БД; изменения `storage` требуют smoke на реальном S3 (RustFS в compose) под новым маркером `s3` и гейтом `HUB_S3_TESTS=1` (регистрация маркера — этап 2); проверить, что формулировка не допускает диалектного расхождения и не ограничивается `LocalBackend`.

## 2. Правила

- [x] 2.1 Заменить в `design.rules` правило «MUST сохранять Navidrome как источник истины» (`:96`) и упоминание Navidrome в адаптерах (`:97`) на «Hub — источник истины для учёток, ♥, плейлистов, истории и каталога; Navidrome не используется»; проверить `openspec validate "navidrome-migration-0-governance"`.
- [x] 2.2 Добавить в `design.rules` правила: Postgres + Alembic, S3-блобы только через `storage`, `auth` — локальные учётки + JWT + OIDC RP, JWT — представление серверной сессии с сохранением `hm_session` до этапа 4, новые объекты библиотеки только через импорт Hub, никаких прямых обращений к ND из доменов, сканирование бакета запрещено; проверить, что правила не противоречат друг другу.
- [x] 2.3 Обновить `proposal.rules` и `tasks.rules`: заменить упоминания SQLite/Navidrome на целевую модель; в `operations.apply.guidance` (`:118`) — «PWA и capability» вместо «PWA и Android», сохранив имя/scope cookie `hm_session` и контракт `/app/*`/`HomeMusicAndroid`; проверить `openspec doctor`.
- [x] 2.4 Убрать Navidrome/Subsonic из `:26-27`, `:64`, переписать `:33-34` как S3-primary + staging/hot-cache на локальном диске; проверить `rg -i "navidrome|subsonic" openspec/config.yaml` — остались только намеренные упоминания.

## 3. Контракт серии

- [x] 3.1 Зафиксировать в context порядок этапов `0 governance → 1 postgres → 2 storage → 3 catalog → 4 identity → 5 user-data → 6 delivery → 7 decommission` и зависимости; проверить по именам каталогов `openspec/changes/navidrome-migration-*`, а не по `openspec list` (он сортирует по времени).
- [x] 3.2 Зафиксировать политику delta-спек: базовые `openspec/specs/*` остаются As-Is до архивации соответствующего change; продублировать в `operations.apply.guidance`; проверить, что формулировка не требует переписывать базовые спеки заранее.
- [x] 3.3 Проверить, что proposal этапов 1–7 уже содержат строку «Зависит от `navidrome-migration-0-governance`»; проверка — `rg -l "navidrome-migration-0-governance" openspec/changes/navidrome-migration-[1-7]-*/proposal.md` возвращает ровно 7 файлов.

## 4. Проверка

- [x] 4.1 Прогнать `openspec validate "navidrome-migration-0-governance"`; ожидается успешная валидация (при `skip_specs: true` — без delta-спек).
- [x] 4.2 Прогнать `openspec doctor`; ожидается отсутствие ошибок конфигурации.
- [x] 4.3 Проверить `git status`: изменён только `openspec/config.yaml` и артефакты change 0; `hub/`, `web/`, `docker-compose.yml`, `.env` не изменены.
- [x] 4.4 Повторно проверить `rg -l "navidrome-migration-0-governance" openspec/changes/navidrome-migration-[1-7]-*/proposal.md`; ожидается 7 файлов (та же проверка, что 3.3).
