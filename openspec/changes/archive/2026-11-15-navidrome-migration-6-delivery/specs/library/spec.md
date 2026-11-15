## ADDED Requirements

### Requirement: Источник каталога для PWA

PWA SHALL получать каталог, обложки и данные артистов из `/api/library/*`. PWA с `hub_library_v1` MUST NOT обращаться к `/api/nd/*`; PWA без capability (старый сервер или таймаут `/healthz`) MAY использовать `/api/nd/*` как deprecated fallback **до этапа 7**. После этапа 7 fallback удаляется.

#### Scenario: Главная PWA
- **WHEN** открывается главная страница
- **THEN** данные приходят из `/api/library/home`

#### Scenario: Альбом и артист
- **WHEN** открывается альбом или артист
- **THEN** данные приходят из `/api/library/albums/{uid}` или `/api/library/artists/{uid}`
- **AND** обложка — из `/api/library/albums/{uid}/cover`

#### Scenario: Плейлист
- **WHEN** открывается плейлист
- **THEN** треки приходят из `/api/playlists/{uid}`
- **AND** библиотечные треки играют по `lib:<uid>`

#### Scenario: Старый сервер
- **WHEN** `/healthz` не содержит `hub_library_v1` или недоступен
- **THEN** PWA использует `/api/nd/*` как deprecated fallback и играет по `nd:<id>`
- **AND** не падает
