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
- [x] 5.3 Снять файловую директиву `reportAttributeAccessIssue`/`reportOptionalMemberAccess` в `tests/test_listen.py` (14 скрытых ошибок): сузить пул до `QueuePool`, заменить `svc.deezer` на типизированные `set_deezer()`/`deezer()`, добавить `assert ... is not None`; ложный комментарий «pyrightconfig includes only app» исправлен. Найдено советом валидации (`council-validate`) как P1.

## 6. Гейт

- [x] 6.1 `hub/pyrightconfig.json`: `include: ["app", "tests"]`, убрать `tests` из `exclude`.
- [x] 6.2 Удалить временный `hub/pyright.tests.json`.
- [x] 6.3 Прогнать `cd hub && uv run pyright` (0 ошибок), `uv run ruff check`, `uv run ruff format --check`.
- [x] 6.4 Прогнать полный `pytest`; число `passed`/`skipped` MUST совпасть с базовой линией (1.2), кроме тестов, добавленных этим change'ем (их нет).
- [x] 6.5 Проверить, что ни один assert не удалён: `git diff` по `hub/tests` не содержит удалённых строк с `assert` без замены; расхождения объяснить в отчёте.

## Меморандум council-validate

**Вопрос и scope:** доведены ли типы тестов до нуля pyright без ослабления тестов и без правок `hub/app/**`.

**Вердикт: FIX-FIRST → PASS после исправления.** Оба советника независимо нашли один и тот же P1.

**P1 (исправлен, коммит `4f2317f`):** в `hub/tests/test_listen.py` оставалась файловая директива `# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false`, скрывавшая 14 ошибок уже после включения `tests` в гейт; комментарий «pyrightconfig includes only app» стал ложным. Директива снята, ошибки устранены сужением типов (`cast(QueuePool, engine().pool)`, типизированные `set_deezer()`/`deezer()`, `assert ... is not None`), ложный комментарий переписан.

**Консенсус:** `hub/app/**` не изменён (diff пуст); подавления в `test_mixes.py`/`test_insights.py` действительно сняты, а не перенесены; `cast(Any, ...)` только для фейков, не наследующих продовый класс; `reportRedeclaration` в `test_ytdlp.py` устранён без изменения поведения.

**Проверка «ни один assert не удалён» (п. 6.5), выполнена родителем:** в `test_listen.py` 4 удалённые строки с `assert` заменены эквивалентными или более строгими проверками, число assert выросло 149 → 151; по всему change'у +91 assert, ни один не ослаблен.

**Отклонено после проверки:** опасение, что `assert x is not None` маскирует баг — это более ранний и явный отказ вместо `AttributeError`, проверки содержимого сохранены.

**Решения владельца:** (1) `set_deezer()` использует `cast(Any, fake)` — допустимо правилом change'а, ужесточение до `cast("DeezerClient", fake)` оставлено как возможный P2; (2) line-level `# pyright: ignore` в `hub/app/api/schemas.py:405` — pre-existing долг app, вне scope.

**Доказательства:** baseline 200 ошибок / 18 файлов; финал `pyright` (`app`+`tests`) → 0; `pytest -q -n 4` → 1163 passed, 74 skipped (мастер; база ветки 1129/74); `test_listen.py` → 37 passed; `ruff` чисто; временные `pyright.tests.json`/`pyright.one.json` удалены. Run ids: pass 1 — `f8009375`; re-check — `c2da8059`.

**Уверенность:** высокая (diff + полный прогон + пересчёт assert). Изменило бы решение: найденный ослабленный assert или изменение `hub/app/**`.

**Ростер:** oracle (context: fork) + reviewer (обычный контекст); 1 проход + 1 re-check после P1; фолбэков не было.
