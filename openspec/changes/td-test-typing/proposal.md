## Why

`uv run pyright` в Hub настроен только на `app/` (`hub/pyrightconfig.json`: `include: ["app"]`), поэтому тесты
никогда не проверялись статически. Долг накопился и маскирует реальные ошибки: в `hub/tests` сейчас **200 ошибок
pyright** в 18 файлах (`reportOptionalMemberAccess`, `reportArgumentType`, `reportRedeclaration` и др.).

Долг уже помешал: при вычистке Navidrome (`navidrome-migration-7-decommission`) тесты содержали неверные типы,
которые проявились только на прогоне, а в двух файлах пришлось поставить файловые директивы
`# pyright: reportOptionalMemberAccess=false, ...` (`test_mixes.py`, `test_insights.py`) — это подавление, а не
исправление. Один настоящий дефект (`test_plays.py`: `**dict[str, str]` перекрывал именованный параметр `at`)
нашёлся только ручным запуском pyright по тестам.

Из-за отсутствия проверки типов регрессии в тестах видны лишь в рантайме, а `cast(Any, ...)` и `getattr`
прячут несовпадение контрактов фикстур с моделями.

## What Changes

- Убрать 200 ошибок pyright в `hub/tests` пофайлово, не ослабляя ни одного assert и не удаляя проверки.
- Снять файловые директивы `# pyright: ...` в `tests/test_mixes.py` и `tests/test_insights.py`.
- Включить `tests` в `hub/pyrightconfig.json` (`include: ["app", "tests"]`) и убрать `tests` из `exclude`, чтобы
  долг не вернулся.
- поведение, API, схема БД, формат ответов, сами проверки тестов не меняются.

## Capabilities

Нет: change помечен `skip_specs: true` — это долг тулинга и типов, требований к системе он не добавляет.
Контракт «статическая проверка типов обязательна» в базовых спеках не зафиксирован, поэтому capability
`maintainability` не заводится (см. `design.md`, решение 2).

## Impact

- Код: только `hub/tests/**` и `hub/pyrightconfig.json`.
- Риск нулевой для продакшена: `app/` не меняется. Риск для тестов — случайно ослабить проверку; поэтому каждая
  правка обязана сохранять смысл и проходить полный `pytest`.
- Проверка: `uv run pyright -p pyright.tests.json` (временный конфиг) до и после, `uv run ruff check`,
  `uv run ruff format --check`, полный `pytest`.
