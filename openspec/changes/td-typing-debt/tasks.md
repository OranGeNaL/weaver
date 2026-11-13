## 1. Инфраструктура проверки

- [ ] 1.1 Добавить `pyright` (закреплённая версия) в dev-зависимости `hub/pyproject.toml`, выполнить `uv lock`.
- [ ] 1.2 Добавить `hub/pyrightconfig.json`: `include: ["app"]`, `typeCheckingMode: "standard"`, версия Python 3.12.
- [ ] 1.3 Зафиксировать базовый список ошибок по группам ниже и команду проверки в документации (`hub/API.md`).

## 2. Доступ к данным и SQLAlchemy

- [ ] 2.1 Заменить `res.rowcount` на явный `RETURNING`/`_rowcount` там, где SQLAlchemy 2.1 типизирует результат как `Result` (`accounts.py`, `api/downloads.py`, `jobs/queue.py` — проверено, `plays.py` — проверено).
- [ ] 2.2 Сузить Optional-поля моделей в запросах (`jobs/downloads.py`: `track_id`, `batch_id`, `svc.deezer`; `imports/core.py`: `Import | None`, `Literal`-поля; `imports/apply.py`: `playlist_id`, `ref`).
- [ ] 2.3 Проверить `hub/app/api/mixes.py`, `hub/app/api/smart_shuffle.py`, `hub/app/api/library.py` на Optional-доступы после перехода на JSONB.

## 3. Импорт и библиотека

- [ ] 3.1 `library/importer.py`: `ReleaseType`/`deezer_*_id` — привести типы к полям моделей.
- [ ] 3.2 `imports/resolve.py`, `imports/apply.py`: сузить `ref: str | None` и словари результатов.
- [ ] 3.3 `library/navidrome.py`, `library/tagger.py`, `library/probe.py`: типы протоколов и `ASYNC`-замечания.

## 4. Фоновые задачи и очередь

- [ ] 4.1 `jobs/downloads.py`: Optional-доступы (`track_id`, `health`, `get_artist*`, `svc.*`).
- [ ] 4.2 `jobs/queue.py`: типизация `_claimable`/`_claim` и `_finish` без `type: ignore`.
- [ ] 4.3 `worker`/`services`: типы реестров провайдеров.

## 5. Рекомендации и аналитика

- [ ] 5.1 `mixes/core.py`, `mixes/taste.py`, `mixes/taste_math.py`, `mixes/taste_snapshot.py`: Optional-`now`, `Subject`/`FavoriteSubject` — протокол вместо `cast`, где возможно.
- [ ] 5.2 `mixes/wave.py`, `mixes/smart.py`, `mixes/build.py`: убрать оставшиеся `cast` после введения протоколов.
- [ ] 5.3 `insights/analytics.py`, `insights/names.py`, `insights/taste_view.py`, `insights/waves_view.py`: Optional-арифметика и `join` по спискам.

## 6. Ассистент и внешние клиенты

- [ ] 6.1 `ai/llm.py`, `ai/agent.py`, `ai/pipeline.py`: единый `httpx.AsyncClient` и типы сообщений/инструментов OpenAI-совместимого API.
- [ ] 6.2 `net.py`, `providers/*`: типы ответов провайдеров и приоритетов сети.
- [ ] 6.3 `listen.py`: Optional-доступы `svc.deezer`/`svc.providers`.

## 7. Проверка

- [ ] 7.1 `pyright` по конфигу репозитория: 0 ошибок.
- [ ] 7.2 `ruff check` и `ruff format --check`: без ошибок.
- [ ] 7.3 `pytest`: поведение не изменилось.
- [ ] 7.4 Убедиться, что новых `cast`/`# type: ignore` не больше, чем оправдано инвариантами (список в PR/коммитах).
