## 1. Инструмент и фиксация

- [x] 1.1 Закрепить `ruff` в dev-зависимостях `hub/pyproject.toml` точной версией, выполнить `uv lock`.
- [x] 1.2 Убедиться, что `[tool.ruff]`/`[tool.ruff.lint]` не меняются ради форматирования (правила и `line-length` остаются).

## 2. Форматирование

- [x] 2.1 Прогнать `ruff format hub/` (`app`, `tests`, `migrations`) одним коммитом `style: ruff format`.
- [x] 2.2 Проверить `git diff -w` после форматирования: нет смысловых изменений, только представление.
- [x] 2.3 Убедиться, что формат не задел `web/`, `embedder/` и сгенерированные файлы (`hub/app/static/**`).

## 3. Проверки

- [x] 3.1 `ruff check` — 0 замечаний.
- [x] 3.2 `ruff format --check` — 0 файлов к переформатированию.
- [x] 3.3 `pytest` на тестовой Postgres — зелёный (база: 1081 passed, 74 skipped).
- [x] 3.4 `npm run typecheck` — без ошибок.

## 4. Документация и история

- [x] 4.1 Добавить `.git-blame-ignore-revs` с хешем форматного коммита и строкой в документации, как его включить.
- [x] 4.2 Описать порядок проверок (`ruff check` → `ruff format --check` → `pytest` → `npm run typecheck`) в `README.md`/`hub/API.md`.
