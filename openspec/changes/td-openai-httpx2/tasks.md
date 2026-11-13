## 1. Подготовка

- [ ] 1.1 Проверить в установленной версии `httpx2`: `AsyncClient(proxy=, timeout=, follow_redirects=)`, `Timeout`, `MockTransport`.
- [ ] 1.2 Зафиксировать `httpx2` (сейчас транзитивный `2.13.1`) прямой зависимостью `hub/pyproject.toml`, выполнить `uv lock`.

## 2. Тесты без привязки к библиотеке

- [ ] 2.1 Переписать моки LLM в `hub/tests/test_ai_agent.py` на `httpx2.MockTransport` (запись запросов в `state["llm_requests"]` сохраняется).
- [ ] 2.2 Убедиться, что тесты зелёные на текущем `httpx`-клиенте (мок от библиотеки не зависит).

## 3. Клиент SDK

- [ ] 3.1 Перевести `LLMClient._http` (`hub/app/ai/llm.py`) на `httpx2.AsyncClient`, сохранив proxy/timeout/follow_redirects.
- [ ] 3.2 Перевести `make_client()` (`hub/app/ai/agent.py`) на `httpx2.AsyncClient`; убрать `cast(Any, http_client)` в обоих местах.
- [ ] 3.3 Добавить контрактный тест: клиент, переданный в `AsyncOpenAI`, — `httpx2.AsyncClient`, и запрос действительно идёт через него.

## 4. Проверка

- [ ] 4.1 `pyright` по `hub/pyrightconfig.json`: 0 ошибок.
- [ ] 4.2 `ruff check` и `ruff format --check`: без ошибок.
- [ ] 4.3 `pytest -q -n 4` на тестовом Postgres: зелёный (база: 1081 passed).
- [ ] 4.4 Ответы ассистента не изменились (те же запросы/формат; `respx`-моки других модулей не тронуты).

## 5. Откат (если API `httpx2` несовместим)

- [ ] 5.1 Записать решение: оставить `httpx` + `cast(Any, ...)` и запинить `openai`; закрыть change с обоснованием.
