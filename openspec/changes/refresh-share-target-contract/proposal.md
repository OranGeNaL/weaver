## Why

`hub/API.md` описывает `/share-target` как серверную обработку изображений и аудио с редиректами на `/#/assistant/{id}` и `/#/upload`. Фактический `api/system.py` только проверяет наличие cookie `hm_session` и редиректит на `/#/login?next=share` или `/#/share?fallback=1`; содержимое обрабатывает PWA через service worker.

## What Changes

- Обновить раздел `/share-target` в `hub/API.md`.
- Зафиксировать фактическое поведение: сервер не разбирает файлы, а только выбирает редирект.
- Убрать обещание серверной обработки изображений/аудио.
- Согласовать документ с `openspec/specs/system/spec.md`.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет. Это изменение документации, поведение системы не меняется.

## Impact

- `hub/API.md`
