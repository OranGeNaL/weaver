## Context

См. `proposal.md — Why`. Сейчас: `LLMClient._http` и `make_client()` создают `httpx.AsyncClient` с
proxy/timeout; `AsyncOpenAI` получает его через `cast(Any, ...)`, потому что типы SDK ждут
`httpx2.AsyncClient`. `respx` в тестах патчит `httpx`, поэтому подмена работает только пока клиент —
`httpx`. Прямой зависимости от `httpx2` в `hub/pyproject.toml` нет (он транзитивный от `openai`).

## Goals / Non-Goals

**Goals:**
- Один честный HTTP-клиент для OpenAI-совместимого API: `httpx2`, без `cast(Any, ...)`.
- Тесты, которые проверяют реальный путь SDK → HTTP, а не только совместимость по случаю.
- Зафиксированная версия `httpx2` и заметка, почему он нужен.

**Non-Goals:**
- Не менять поведение ассистента, промпты, формат запросов/ответов и таймауты.
- Не переводить остальной HTTP Hub (`app/net.py`, провайдеры) на `httpx2`.
- Не трогать `respx`-моки модулей, которые остаются на `httpx`:
  - провайдеры — hitmoz/hitmos.fm, Deezer, MusicBrainz, yt-dlp/YouTube;
  - внешние источники и адаптеры — mix sources (YTM, Last.fm, Deezer, embedder),
    Yandex OAuth при импорте, OIDC IdP, Spotify-ссылки в `ai_links`, `net`.

## Decisions

### 1. `httpx2` как прямой клиент SDK

`httpx2` — HTTP-библиотека, на которой построен `openai` 3.x; использовать её для клиента SDK —
единственный способ убрать `cast(Any, ...)` и остаться совместимым при обновлении SDK. Версия
фиксируется в `pyproject.toml` (сейчас `2.13.1`), обновление — вместе с `openai`.

Альтернатива — оставить `httpx` и запинить `openai` с комментарием — отвергнута: несовпадение типов
останется скрытым, а обновление SDK его проявит.

### 2. Моки тестов

`respx` патчит `httpx`, поэтому для `httpx2` нужен другой способ: подменить `http_client` на
`httpx2.AsyncClient(transport=MockTransport(...))` в фикстуре или подменить сам `AsyncOpenAI` фабрикой.
Выбор — по объёму: если `respx` новой версии умеет `httpx2`, использовать его; иначе `MockTransport`
(он есть в обоих форках) и запись запросов в `state["llm_requests"]` как сейчас.

### 3. Порядок и откат

Сначала тесты на `MockTransport` (они не зависят от библиотеки), затем перевод клиентов, затем
удаление `cast`. Откат — вернуть `httpx.AsyncClient` и `cast(Any, ...)` одним коммитом; поведение
не меняется ни в одну сторону.

## Risks / Trade-offs

- [`httpx2` — форк `httpx`, API может разойтись] → версия зафиксирована, контрактный тест ловит смену типа клиента.
- [Моки станут сложнее `respx`] → `MockTransport` точечно в одной фикстуре `test_ai_agent.py`.
- [Дублирование HTTP-зависимостей (`httpx` и `httpx2`)] → `httpx2` только для клиента SDK, остальное — `httpx`.
- [Забыть про прокси-семантику] → в тестах проверить, что `http_client` создаётся с тем же proxy/timeout.

## Migration Plan

1. Проверить API `httpx2` (`AsyncClient`, `Timeout`, `MockTransport`) в установленной версии.
2. Переписать моки `test_ai_agent.py` на `MockTransport`; тесты зелёные на текущем `httpx`-клиенте (мок не зависит от библиотеки).
3. Перевести `llm.py`/`agent.py` на `httpx2.AsyncClient`, убрать `cast(Any, ...)`.
4. Добавить `httpx2` в зависимости, `uv lock`.
5. `pyright` (0), `ruff`, `pytest`; контрактный тест на тип клиента.

Откат: `git revert` шагов 2–4; данные и API не затронуты.

## Open Questions

Нет.
