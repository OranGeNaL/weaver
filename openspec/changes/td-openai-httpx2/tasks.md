## 1. Подготовка

- [x] 1.1 Проверить в установленной версии `httpx2`: `AsyncClient(proxy=, timeout=, follow_redirects=)`, `Timeout`, `MockTransport`.
- [x] 1.2 Зафиксировать `httpx2` (сейчас транзитивный `2.13.1`) прямой зависимостью `hub/pyproject.toml`, выполнить `uv lock`.

## 2. Тесты без привязки к библиотеке

- [x] 2.1 Переписать моки LLM в `hub/tests/test_ai_agent.py` на `httpx2.MockTransport` (запись запросов в `state["llm_requests"]` сохраняется).
- [x] 2.2 Убедиться, что тесты зелёные на текущем `httpx`-клиенте: промежуточная версия мока работала через `httpx` и не зависела от библиотеки. Итоговый мок привязан к `httpx2` (патчит `httpx2.AsyncHTTPTransport`), потому что приложение перешло на этот клиент.

## 3. Клиент SDK

- [x] 3.1 Перевести `LLMClient._http` (`hub/app/ai/llm.py`) на `httpx2.AsyncClient`, сохранив proxy/timeout/follow_redirects.
- [x] 3.2 Перевести `make_client()` (`hub/app/ai/agent.py`) на `httpx2.AsyncClient`; убрать `cast(Any, http_client)` в обоих местах.
- [x] 3.3 Добавить контрактный тест: клиент, переданный в `AsyncOpenAI`, — `httpx2.AsyncClient`, и запрос действительно идёт через него.

## 4. Проверка

- [x] 4.1 `pyright` по `hub/pyrightconfig.json`: 0 ошибок.
- [x] 4.2 `ruff check` и `ruff format --check`: без ошибок.
- [x] 4.3 `pytest -q -n 4` на тестовом Postgres: зелёный (база: 1129 passed, 74 skipped; на ветке change 1131 passed после двух новых контрактных тестов).
- [x] 4.4 Ответы ассистента не изменились (те же запросы/формат; `respx`-моки других модулей не тронуты).

## 5. Откат (если API `httpx2` несовместим)

- [x] 5.1 Записать решение: оставить `httpx` + `cast(Any, ...)` и запинить `openai`; закрыть change с обоснованием. — не потребовалось: в `httpx2` 2.13.1 есть `AsyncClient(proxy=, timeout=, follow_redirects=)`, `Timeout` и `MockTransport` с тем же API, что у `httpx`, поэтому change закрыт реализацией (см. 1.1, 3.1, 4.3).

## Меморандум council-validate

**Вопрос и scope:** корректно ли реализован `td-openai-httpx2` (клиент SDK на `httpx2`, прямые зависимости, моки и контрактные тесты) до архивации.

**Вердикт: PASS.** Блокеров и P1 нет; P2-правки внесены, повторный проход их принял.

**Консенсус:** оба советника (2/2) подтвердили: `httpx2==2.13.1` — одна версия в lock и совпадает с требованием `openai==3.35.1`; `LLMClient._http` и `make_client()` строят `httpx2.AsyncClient`, `cast(Any, http_client)` удалён; pyright действительно ловит несовпадение (`AsyncOpenAI.http_client: httpx2.AsyncClient`); контрактные тесты не тавтологичны и падали бы на прежней реализации.

**Принятые P2:** устаревшая база `1081` и формулировка 2.2 в tasks.md исправлены; контрактный тест `make_client()` ужесточён с `isinstance(...)` до `type(...) is httpx2.AsyncClient` (коммит `96f0258`) — иначе удаление `http_client=` осталось бы незамеченным.

**Отклонено после проверки:** гипотеза «глобальный патч транспорта маскирует неверный клиент» — identity/isinstance-проверки от мока не зависят.

**Решения владельца:** (1) тест на proxy/follow_redirects не добавлялся — поведение читается в `llm.py` и подтверждено полным прогоном; вынести в отдельный `td-*` при необходимости; (2) приватные атрибуты SDK (`_client._client`) в контрактных тестах допустимы при закреплённом `openai==3.35.1`.

**Доказательства:** `pytest -q -n 4` → 1163 passed, 74 skipped (мастер); точечно `test_ai_agent.py` + `test_ai_extract.py` → 63 passed; `pyright` → 0; `ruff check`/`format --check` → чисто; `uv lock` согласован. Run ids: pass 1 — `e362b534`, cross-exam не требовался; re-check — `c2da8059`.

**Уверенность:** высокая (сигнатуры установленных пакетов + lock + тесты). Изменило бы решение: изменение семантики proxy/redirect на `httpx2` или требование единственного HTTP-стека в архитектуре.

**Ростер:** oracle (context: fork) + reviewer (обычный контекст); 1 проход + 1 re-check после правок; фолбэков не было.
