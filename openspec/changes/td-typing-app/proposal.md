## Why

`td-test-typing` включил `tests` в гейт `pyright` и довёл тесты до нуля, но в `app/**` осталось ровно одно
подавление: `app/api/schemas.py:405` — `# pyright: ignore[reportIncompatibleVariableOverride]` на
`AlbumOut.tracks`. Оно маскирует единственную ошибку типов во всём прикладном коде: `AlbumOut` сужает
`CatalogAlbum.tracks: list[CatalogTrack] | None` до `list[TrackOut] | None`, хотя `TrackOut` — наследник
`CatalogTrack` и `list` инвариантен. Пока подавление на месте, гейт «app без исключений» неполон: новая
несовместимая переопределённая колонка/поле в API-схемах пройдёт молча, как раньше проходили ошибки в тестах.

## What Changes

- Убрать `# pyright: ignore[reportIncompatibleVariableOverride]` в `app/api/schemas.py`, устранив причину
  ошибки типов, а не подавление.
- Привести связь `CatalogAlbum` → `AlbumOut` к типово-корректной модели: параметризованная база
  (`CatalogAlbum[TTrack]`) либо отказ от наследования в пользу композиции — выбор и обоснование в `design.md`.
- Сохранить форму ответа API: `GET /api/catalog/album/{ref}` и ответы `app/library/owned.py` продолжают отдавать
  треки с полями `TrackOut` (`owned`, `library_track_id`, `uid`, `downloading`, `star_pending`), а OpenAPI-схема
  не теряет этих полей.
- Зафиксировать гейт: `pyright` по `hub/pyrightconfig.json` (`include: ["app", "tests"]`) — 0 ошибок и
  **0 подавлений** в `app/**`; при появлении нового `# pyright: ignore` в `app/**` проверка должна падать
  (тест, сканирующий `app/**` на директивы).

## Capabilities

### New Capabilities

Нет: change помечен `skip_specs: true` — это долг типов, поведение системы и контракт ответов не меняются.

### Modified Capabilities

Нет.

## Impact

- Код: `hub/app/catalog/models.py` (`CatalogAlbum` и его потребители), `hub/app/api/schemas.py` (`AlbumOut`,
  `TrackOut`), возможно `hub/app/library/owned.py`, `hub/app/api/catalog.py`, `hub/app/catalog/*_catalog.py`
  (специализация параметризованной базы).
- API: JSON-ответы и OpenAPI **не меняются**; при выборе композиции — отдельная проверка формы ответа снимком.
- БД, фоновые задачи, клиенты: без изменений.
- Проверка: `pyright` (0 ошибок, 0 подавлений в `app/**`), `ruff check`, `ruff format --check`,
  `pytest -q -n 4` (число `passed`/`skipped` совпадает с базовой линией), снимок OpenAPI/ответа альбома
  до и после.
