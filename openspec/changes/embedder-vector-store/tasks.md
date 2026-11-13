## 0. Рамки

- [ ] 0.1 Снять `skip_specs` и добавить delta-спеки `recommendations` (деградация/`sim`) и `system` (карта вкуса) до начала реализации; проверить `openspec validate`.
- [ ] 0.2 Зафиксировать зависимость от `1-postgres`/`2-storage`/`3-catalog` и владельца векторов — `recommendations`; проверить в proposal/design.

## 1. Исследование

- [ ] 1.1 Проверить `pgvector` на целевой версии Postgres (установка, индексы, лимиты размерности); зафиксировать вывод в design.
- [ ] 1.2 Замерить объём и скорость текущих запросов похожести embedder; определить целевые бюджеты.
- [ ] 1.3 Решить, нужен ли fallback без pgvector; зафиксировать в design.
- [ ] 1.4 Зафиксировать очередь векторов в Hub (`job kind` + `@handler`) и границу транзакции записи; проверить, что embedder не хранит очередь.
- [ ] 1.5 Сравнить exact scan и ANN (HNSW/IVFFlat): recall, задержка, допустимая разница; зафиксировать tolerance для parity-теста.

## 2. Схема

- [ ] 2.1 Добавить расширение `vector`, таблицы `embedding_spaces` (`model`, `geometry_version`, `snapshot_digest`, `UNIQUE(model, geometry_version)`) и `embeddings` (`space_id` FK, `key`, nullable `track_uid`, `blob_sha256`, `model`, `UNIQUE(space_id,key)`) и атомарную публикацию `snapshot_digest`; проверить `alembic upgrade head` и откат.
- [ ] 2.2 Добавить индексы для поиска похожести; проверить план запроса.

## 3. Embedder и Hub

- [ ] 3.1 Перевести embedder на stateless-режим: приём аудио/URL → возврат вектора; проверить контракт.
- [ ] 3.2 (Hub/`recommendations`) Реализовать запись вектора и проверить, что повторная запись не создаёт новую `embedding_spaces` в Postgres и идемпотентность по `(key, blob_sha256, model)`; один ряд на `(space_id, key)`.
- [ ] 3.3 (Hub/`recommendations`) Реализовать пересчёт/бэкфилл векторов (не миграцию) идемпотентно по `(key, blob_sha256, model)`; проверить возобновление после сбоя и частичный сбой.
- [ ] 3.4 Сохранить `/v2/*` и внутренние `/text`, `/embed/{key}`, `/similarity`, `/vectors`, `/v2/shadow`; владелец store-backed путей — Hub; `POST /embed` пометить deprecated и удалить после перехода; проверить контракт Hub.

## 4. Рекомендации

- [ ] 4.1 Перевести профили вкуса и похожесть на SQL-запросы; проверить parity на фикстурах с зафиксированным tolerance (exact или ANN).
- [ ] 4.2 Проверить деградацию без embedder/pgvector: рекомендации работают без звукового признака.

## 5. Проверка

- [ ] 5.1 Прогнать `pytest` и `ruff`; ожидается зелёный прогон.
- [ ] 5.2 Проверить миграцию `upgrade`/`downgrade` и бэкфилл на тестовом наборе.
- [ ] 5.3 Обновить README/ARCHITECTURE в части хранения векторов.
- [ ] 5.4 Проверить обратную совместимость v1-endpoint'ов и откат на in-process-хранилище; зафиксировать в design.
