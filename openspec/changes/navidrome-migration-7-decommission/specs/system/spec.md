## MODIFIED Requirements

### Requirement: Публичный health и capabilities

Hub SHALL отдавать `GET /healthz` без авторизации с признаком доступности, версией, идентификатором сборки PWA и списком capabilities. Capabilities MUST позволять клиентам выбирать поддерживаемые функции без угадывания версии Hub. Признак `navidrome` MUST отсутствовать.

#### Scenario: Hub доступен
- **WHEN** клиент вызывает `/healthz`
- **THEN** Hub возвращает `ok: true`
- **AND** возвращает `web_build` и список capabilities
- **AND** не возвращает поле `navidrome`

#### Scenario: Navidrome недоступен
- **WHEN** внешний медиасервер отсутствует (Navidrome выведен из проекта)
- **THEN** `/healthz` возвращает `ok: true`
- **AND** состав ответа не зависит от внешнего медиасервера

#### Scenario: Новый клиент
- **WHEN** клиент видит `typed_favorites_v1`, `listening_checkpoints_v1`, `launch_events_v1`, `wave_origin_v1`, `smart_shuffle_v1`, `hub_library_v1` или `hub_liked_v1`
- **THEN** он MAY использовать соответствующее расширение API
- **AND** старый клиент продолжает работать без него

## ADDED Requirements

### Requirement: Удаление публичных URL Navidrome

Hub SHALL удалить `public_music_url` из `MeOut`/`/api/auth/me`, `music_url` из `/api/auth/options`, а также `player_seen_at` из админских ответов. `public_hub_url` MUST остаться.

#### Scenario: Ответ входа
- **WHEN** клиент вызывает `/api/auth/me`
- **THEN** ответ содержит `public_hub_url`
- **AND** не содержит `public_music_url`/`music_url`

#### Scenario: Админский список
- **WHEN** администратор вызывает `/api/admin/users`
- **THEN** в ответе нет `player_seen_at`
