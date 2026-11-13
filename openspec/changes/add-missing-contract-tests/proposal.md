## Why

Часть контрактов из базовых спецификаций не покрыта тестами. Это нарушает правило `openspec/config.yaml`: изменение поведения MUST сопровождаться тестом. Сейчас без тестов остаются:

- `PUT/GET /api/library/albums/{id}/cover`: 413, `?size`, 404;
- `POST /share-target`;
- SSE `ping` и медленный клиент;
- `run_daily`, `purge_trash`, `backup_hub_db`.

## What Changes

- Добавить тесты на существующее поведение без изменения production-кода.
- Покрыть обложки альбомов: успешную загрузку, ресайз, отсутствие обложки, слишком большую картинку.
- Покрыть `/share-target`: без cookie, с cookie.
- Покрыть SSE: первый `ping`, периодический `ping`, отбрасывание сообщений при заполненной очереди.
- Покрыть housekeeping: `run_daily` при ошибках, `purge_trash`, `backup_hub_db`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет. Поведение не меняется, добавляются только тесты.

## Impact

- `hub/tests/`
- возможно небольшие test-only helpers в `hub/tests/support.py`
