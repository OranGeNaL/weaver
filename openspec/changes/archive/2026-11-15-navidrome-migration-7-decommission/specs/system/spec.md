## ADDED Requirements

### Requirement: Публичный health и capabilities Hub

Hub SHALL отдавать `GET /healthz` без авторизации с признаком доступности, версией, идентификатором сборки PWA и списком capabilities. Capabilities MUST позволять клиентам выбирать поддерживаемые функции без угадывания версии Hub. Состав ответа MUST NOT зависеть от внешнего медиасервера.

#### Scenario: Hub доступен
- **WHEN** клиент вызывает `/healthz`
- **THEN** Hub возвращает `ok: true`
- **AND** возвращает `web_build` и список capabilities
- **AND** не сообщает признак внешнего медиасервера

#### Scenario: Внешний медиасервер отсутствует
- **WHEN** внешнего медиасервера в системе нет
- **THEN** `/healthz` возвращает `ok: true`
- **AND** состав ответа не зависит от внешнего медиасервера

#### Scenario: Новый клиент
- **WHEN** клиент видит `typed_favorites_v1`, `listening_checkpoints_v1`, `launch_events_v1`, `wave_origin_v1`, `smart_shuffle_v1`, `hub_library_v1` или `hub_liked_v1`
- **THEN** он MAY использовать соответствующее расширение API
- **AND** старый клиент продолжает работать без него

### Requirement: Публичные адреса Hub

Hub SHALL удалить `public_music_url` из `MeOut`/`/api/auth/me`, `music_url` из `/api/auth/options`, а также `player_seen_at` из админских ответов. `public_hub_url` MUST остаться.

#### Scenario: Ответ входа
- **WHEN** клиент вызывает `/api/auth/me`
- **THEN** ответ содержит `public_hub_url`
- **AND** не содержит `public_music_url`/`music_url`

#### Scenario: Админский список
- **WHEN** администратор вызывает `/api/admin/users`
- **THEN** в ответе нет `player_seen_at`

## MODIFIED Requirements

### Requirement: Capability каталога Hub

Hub SHALL сообщать в `/healthz.capabilities` возможность `hub_library_v1`, означающую, что каталог, обложки и библиотечное прослушивание доступны через `/api/library/*` и `/api/listen?ref=lib:<uid>`.

#### Scenario: Новый клиент
- **WHEN** клиент видит `hub_library_v1`
- **THEN** он MAY использовать `/api/library/*` и `lib:<uid>`
- **AND** использует только `/api/library/*`

#### Scenario: Старый клиент
- **WHEN** клиент не знает `hub_library_v1`
- **THEN** он продолжает работать без расширений каталога (остальной API доступен)

## REMOVED Requirements

### Requirement: Публичный health и capabilities

**Reason**: внешний медиасервер выведен из проекта; ответ `/healthz` больше не сообщает его признак. Требование заменено на «Публичный health и capabilities Hub».
**Migration**: клиенты читают `/healthz` без признака внешнего медиасервера; остальные поля и capabilities не изменились.
