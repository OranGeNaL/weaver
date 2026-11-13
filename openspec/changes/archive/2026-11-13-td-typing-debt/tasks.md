## 1. Инфраструктура проверки

- [x] 1.1 Добавить `pyright` (закреплённая версия) в dev-зависимости `hub/pyproject.toml`, выполнить `uv lock`.
- [x] 1.2 Добавить `hub/pyrightconfig.json`: `include: ["app"]`, `typeCheckingMode: "standard"`, версия Python 3.12.
- [x] 1.3 Зафиксировать базовый список ошибок по группам ниже и команду проверки в документации (`hub/API.md`).

## 2. Доступ к данным и SQLAlchemy

- [x] 2.1 Заменить `res.rowcount` на явный `RETURNING`/`_rowcount` там, где SQLAlchemy 2.1 типизирует результат как `Result` (`accounts.py`, `api/downloads.py`, `jobs/queue.py` — проверено, `plays.py` — проверено).
- [x] 2.2 Сузить Optional-поля моделей в запросах (`jobs/downloads.py`: `track_id`, `batch_id`, `svc.deezer`; `imports/core.py`: `Import | None`, `Literal`-поля; `imports/apply.py`: `playlist_id`, `ref`).
- [x] 2.3 Проверить `hub/app/api/mixes.py`, `hub/app/api/smart_shuffle.py`, `hub/app/api/library.py` на Optional-доступы после перехода на JSONB.

## 3. Импорт и библиотека

- [x] 3.1 `library/importer.py`: `ReleaseType`/`deezer_*_id` — привести типы к полям моделей.
- [x] 3.2 `imports/resolve.py`, `imports/apply.py`: сузить `ref: str | None` и словари результатов.
- [x] 3.3 `library/navidrome.py`, `library/tagger.py`, `library/probe.py`: типы протоколов и `ASYNC`-замечания.

## 4. Фоновые задачи и очередь

- [x] 4.1 `jobs/downloads.py`: Optional-доступы (`track_id`, `health`, `get_artist*`, `svc.*`).
- [x] 4.2 `jobs/queue.py`: типизация `_claimable`/`_claim` и `_finish` без `type: ignore`.
- [x] 4.3 `worker`/`services`: типы реестров провайдеров.

## 5. Рекомендации и аналитика

- [x] 5.1 `mixes/core.py`, `mixes/taste.py`, `mixes/taste_math.py`, `mixes/taste_snapshot.py`: Optional-`now`, `Subject`/`FavoriteSubject` — протокол вместо `cast`, где возможно.
- [x] 5.2 `mixes/wave.py`, `mixes/smart.py`, `mixes/build.py`: убрать оставшиеся `cast` после введения протоколов.
- [x] 5.3 `insights/analytics.py`, `insights/names.py`, `insights/taste_view.py`, `insights/waves_view.py`: Optional-арифметика и `join` по спискам.

## 6. Ассистент и внешние клиенты

- [x] 6.1 `ai/llm.py`, `ai/agent.py`, `ai/pipeline.py`: единый `httpx.AsyncClient` и типы сообщений/инструментов OpenAI-совместимого API.
- [x] 6.2 `net.py`, `providers/*`: типы ответов провайдеров и приоритетов сети.
- [x] 6.3 `listen.py`: Optional-доступы `svc.deezer`/`svc.providers`.

## 7. Проверка

- [x] 7.1 `pyright` по конфигу репозитория: 0 ошибок.
- [x] 7.2 `ruff check` и `ruff format --check`: без ошибок.
- [x] 7.3 `pytest`: поведение не изменилось.
- [x] 7.4 Убедиться, что новых `cast`/`# type: ignore` не больше, чем оправдано инвариантами (список в PR/коммитах).

## 8. Итог валидации совета (Pass 1)

- [x] 8.1 `Hit.of`: `str(... or "")` откатан на `cast(str, ...)` — краевой случай сохранён.
- [x] 8.2 `TagData.deezer_*`: `str | int | None` (int приходит из тестов/каталога), `_to_canonical` по-прежнему пишет int.
- [x] 8.3 `InsightsTasteTrackRow.artist`: `str | dict | None` на отдельной базе без `artist: Any` (в ответах бывает и имя, и блок метаданных).
- [x] 8.4 Дубли `if imp is None` удалены (5 мест).
- [x] 8.5 Касты «текст БД → Literal» снабжены инвариантом (значения пишет только приложение).
- [x] 8.6 `fold`/`AliasIndex`: протокол невозможен (SQLAlchemy `Mapped[...]` под Pyright), сигнатура `Iterable[Any]` с обоснованием; каст на стороне вызова убран.
- [x] 8.7 Верификация: задачи по файлам, которых нет в diff, подтверждены полным `pyright` по `app/` (0 ошибок), а не построчным diff.

## 9. Решения владельца по итогам валидации

- [x] 9.1 Literal-поля БД (`imports.source/method/mode`, `import_collections.kind`, `albums.release_type`, `mix_settings.mode`, `mixes.mode`): вынесены в отдельный change `td-enum-check-constraints` (CHECK-ограничения Postgres, миграция и тесты паритета); в этом change'е инвариант зафиксирован комментариями у кастов.
- [x] 9.2 `hub/tests` в `pyrightconfig` не включаем: анализ покрывает `app/`, как записано в delta-спеке; проверка тестов — `pytest` и `ruff`.
- [x] 9.3 Протокол для субъектов (`FavoriteSubject` / лёгкий `Subject`) не вводится — решение владельца: оставляем текущий вариант. Причина: SQLAlchemy `Mapped[...]` не совместим со структурным Protocol под Pyright; сигнатура `Iterable[Any]` задокументирована, каст на стороне вызова убран.
- [x] 9.4 Клиент OpenAI на `httpx2`: вынесен в отдельный change `td-openai-httpx2` (тулинг/совместимость SDK), чтобы не смешивать с типизацией.
