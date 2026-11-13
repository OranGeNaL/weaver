## Context

См. `proposal.md — Why`. Сейчас `embedder` хранит векторы и очередь в собственной БД (`items`), а Hub обращается к `/embed`, `/similarity`, `/vectors`, `/text` и `/v2/shadow`. Change «припаркован»: код не меняется. `skip_specs: true` — временный маркер, а не решение.

## Goals / Non-Goals

**Goals:**
- Долговременное хранение векторов в Postgres (pgvector), переживающее рестарт.
- Сделать embedder stateless по хранению (счётчик/энкодер остаётся).
- Сохранить смысл рекомендаций; изменить только место хранения.

**Non-Goals:**
- Не менять алгоритм вкуса по смыслу.
- Не реализовывать сейчас; не блокировать `navidrome-migration-*`.
- Не строить новый сервис.

## Decisions

### 1. Владелец и зависимость

Владелец векторов — домен `recommendations`; очередь — Hub (`job kind` + `@handler`), embedder stateless по хранению. Зависимости: `1-postgres` (инстанс/базовые миграции), `2-storage` (URL/кэш), `3-catalog` (`uid`); расширение `vector` создаёт этот change. Выполнять после этапа 3.

### 2. Схема

`embedding_spaces(id, model, geometry_version, snapshot_digest, state, created_at)` с `UNIQUE(model, geometry_version)`; `geometry_version` бампается только при несовместимом изменении геометрии (расширение `vector` создаётся нефатально: при недоступности таблицы не создаются, звуковой признак деградирует до `sim: null`) и `embeddings(space_id FK→embedding_spaces.id, key text, track_uid, blob_sha256, model, vector, updated_at)`, где `key` — сырой ключ embedder (`dz:…`, `lib:<uid>`), `track_uid` — заполняется для `lib:<uid>`, NULL для `dz:`; `UNIQUE(space_id, key)`. `space_id` — неизменяемая идентичность (model + geometry_version); `snapshot_digest` — sha256 по длинам/ключам/float32-векторам, используется для 409 `stale_space` и публикуется атомарно. Запись идёт в pending-revision, `snapshot_digest` не входит в ключ строки, поэтому upsert не ротирует пространство. Индекс — HNSW/IVFFlat после замера.

### 3. Exact vs ANN

Текущая геометрия — центрированные residuals, weighted facets/negatives, PCA. ANN меняет результат, поэтому parity-тест сравнивает с tolerance, а не на равенство; exact scan остаётся fallback.

### 4. Stateless embedder

Embedder принимает аудио/URL и возвращает вектор; запись/чтение — в Postgres. Очередь — Hub job kind `embedding_backfill`; `/v2/profile|similarity|vectors|project` — контракт Hub; `/text`, `/embed/{key}`, `/vectors`, `/similarity`, `/v2/shadow` — временно обслуживаются Hub/read-only store, `POST /embed` становится deprecated (enqueue переезжает в Hub) и удаляется вместе с переходным периодом. Владелец store-backed путей на время перехода — Hub (`recommendations`); embedder не читает хранилище. Idempotency key — `(key, blob_sha256, model)`, не URL и не `space_id`: presigned URL меняется на каждый вызов, а digest зависит от самих записей. Ошибки — русский `detail`, ретрай с backoff, частичный сбой не публикует revision.

### 5. Деградация

Если pgvector недоступен, звуковой признак отключается (`sim: null`), рекомендации работают без него. Это фиксируется delta-спекой `recommendations` после снятия `skip_specs`.

## Risks / Trade-offs

- [ANN меняет результаты] → tolerance-тест и exact fallback.
- [Дублирование v1/v2] → переходный период ограничен.
- [pgvector недоступен] → деградация без звукового признака.

## Migration Plan

1. Снять `skip_specs`, добавить delta-спеки.
2. Замерить и выбрать exact/ANN.
3. Добавить схему/индекс и атомарную публикацию `snapshot_digest`.
4. Перевести embedder и Hub; сохранить `/v2/*`.
5. Пересчитать векторы бэкфиллом и прогнать parity-тесты.
6. Откат — вернуть in-process-хранилище.

## Open Questions

- Exact или ANN (решается в 1.5).
