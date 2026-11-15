## Why

`openai==3.35.1` построен на `httpx2` (транзитивная зависимость `httpx2==2.13.1`), а Hub создаёт
`httpx.AsyncClient` и передаёт его в `AsyncOpenAI(http_client=cast(Any, ...))`. Проверка типов это
не ловит: `cast(Any, ...)` скрывает несовпадение. Если SDK изменит ожидаемый клиент (или перестанет
принимать legacy `httpx`), интеграция сломается в рантайме без ошибки типов. Тесты тоже не страхуют:
они мокают HTTP через `respx`, который перехватывает `httpx`, а не `httpx2`.

## What Changes

- Перевести клиентов `app/ai/llm.py` и `app/ai/agent.py` на `httpx2.AsyncClient` (тот же API: proxy, timeout, follow_redirects) и убрать `cast(Any, http_client)`.
- Добавить `httpx2` прямой dev/runtime-зависимостью с фиксацией версии (сейчас он приходит транзитивно от `openai`).
- Переписать моки LLM в `hub/tests/test_ai_agent.py` на транспорт, совместимый с `httpx2` (или на подмену `AsyncOpenAI`/`http_client`), сохранив текущие проверки запросов.
- Добавить контрактный тест: клиент, который получает SDK, — именно `httpx2.AsyncClient` (или эквивалент), и запросы реально идут через него.
- Поведение, API, БД и формат ответов не меняются.

## Capabilities

Нет: change помечен `skip_specs: true` (внутренняя совместимость с SDK, требований к системе не добавляет).

## Impact

- Код: `hub/app/ai/llm.py`, `hub/app/ai/agent.py`, `hub/tests/test_ai_agent.py`.
- Зависимости: `hub/pyproject.toml` (+`httpx2`), `hub/uv.lock`.
- API, БД, клиенты, фоновые задачи: без изменений.
- Проверка: `pyright` (0), `ruff check`/`format --check`, `pytest tests/test_ai_agent.py` и полный прогон.
