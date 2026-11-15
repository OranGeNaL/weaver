## 1. Подготовка

- [x] 1.1 Проверить в установленной версии `httpx2`: `AsyncClient(proxy=, timeout=, follow_redirects=)`, `Timeout`, `MockTransport`.
- [x] 1.2 Зафиксировать `httpx2` (сейчас транзитивный `2.13.1`) прямой зависимостью `hub/pyproject.toml`, выполнить `uv lock`.

## 2. Тесты без привязки к библиотеке

- [x] 2.1 Переписать моки LLM в `hub/tests/test_ai_agent.py` на `httpx2.MockTransport` (запись запросов в `state["llm_requests"]` сохраняется).
- [x] 2.2 Убедиться, что тесты зелёные на текущем `httpx`-клиенте (мок от библиотеки не зависит).

## 3. Клиент SDK

- [x] 3.1 Перевести `LLMClient._http` (`hub/app/ai/llm.py`) на `httpx2.AsyncClient`, сохранив proxy/timeout/follow_redirects.
- [x] 3.2 Перевести `make_client()` (`hub/app/ai/agent.py`) на `httpx2.AsyncClient`; убрать `cast(Any, http_client)` в обоих местах.
- [x] 3.3 Добавить контрактный тест: клиент, переданный в `AsyncOpenAI`, — `httpx2.AsyncClient`, и запрос действительно идёт через него.

## 4. Проверка

- [x] 4.1 `pyright` по `hub/pyrightconfig.json`: 0 ошибок.
- [x] 4.2 `ruff check` и `ruff format --check`: без ошибок.
- [x] 4.3 `pytest -q -n 4` на тестовом Postgres: зелёный (база: 1081 passed).
- [x] 4.4 Ответы ассистента не изменились (те же запросы/формат; `respx`-моки других модулей не тронуты).

## 5. Откат (если API `httpx2` несовместим)

- [x] 5.1 Записать решение: оставить `httpx` + `cast(Any, ...)` и запинить `openai`; закрыть change с обоснованием. — не потребовалось: в `httpx2` 2.13.1 есть `AsyncClient(proxy=, timeout=, follow_redirects=)`, `Timeout` и `MockTransport` с тем же API, что у `httpx`, поэтому change закрыт реализацией (см. 1.1, 3.1, 4.3).
