## 1. Базовая линия

- [x] 1.1 Зафиксировать число ошибок: временный `hub/pyright.tests.json` (`include: ["tests"]`) + `uv run pyright -p pyright.tests.json`; сохранить список файлов и количество в отчёте (на 2026-11-15 — 200 ошибок в 18 файлах).
- [x] 1.2 Убедиться, что до правок `pytest` зелёный; зафиксировать число `passed`/`skipped`.

## 2. Крупнейшие файлы (89 из 200)

- [x] 2.1 `tests/test_tagger.py` (49): убрать `reportOptionalMemberAccess`/`reportArgumentType` сужением типов и `assert ... is not None`; без ослабления assert'ов.
- [x] 2.2 `tests/test_wave.py` (31): то же.
- [x] 2.3 `tests/test_ai_links.py` (29): то же; `cast(Any, ...)` — только для фейков, не наследующих продовый класс.

## 3. Средние файлы (54 из 200)

- [x] 3.1 `tests/test_imports.py` (18).
- [x] 3.2 `tests/test_flow.py` (11).
- [x] 3.3 `tests/test_youtube_access.py` (10) и `tests/test_ytdlp.py` (9, `reportRedeclaration` — переименовать перекрытую функцию/фикстуру).
- [x] 3.4 `tests/test_yandex_origin.py` (7), `tests/test_taste_consumers.py` (7), `tests/test_import_yandex.py` (7).

## 4. Малые файлы (33 из 200)

- [x] 4.1 `tests/test_taste_shadow.py` (5), `tests/test_smart.py` (5).
- [x] 4.2 `tests/test_ai_limits.py` (3), `tests/test_ai_agent.py` (3).
- [x] 4.3 `tests/test_deezer.py` (2), `tests/test_ai_extract.py` (2).
- [x] 4.4 `tests/test_mix_build.py` (1), `tests/test_hitmos_fm.py` (1).

## 5. Снятие подавлений

- [x] 5.1 Снять `# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false, reportArgumentType=false` в `tests/test_mixes.py`, добиться локального нуля.
- [x] 5.2 Снять ту же директиву в `tests/test_insights.py`; проверить, что `include: ["app"]` пока не сломан.

## 6. Гейт

- [x] 6.1 `hub/pyrightconfig.json`: `include: ["app", "tests"]`, убрать `tests` из `exclude`.
- [x] 6.2 Удалить временный `hub/pyright.tests.json`.
- [x] 6.3 Прогнать `cd hub && uv run pyright` (0 ошибок), `uv run ruff check`, `uv run ruff format --check`.
- [ ] 6.4 Прогнать полный `pytest`; число `passed`/`skipped` MUST совпасть с базовой линией (1.2), кроме тестов, добавленных этим change'ем (их нет).
- [ ] 6.5 Проверить, что ни один assert не удалён: `git diff` по `hub/tests` не содержит удалённых строк с `assert` без замены; расхождения объяснить в отчёте.
