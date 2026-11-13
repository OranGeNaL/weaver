## ADDED Requirements

### Requirement: Удаление переходного слоя Navidrome

Hub SHALL предоставлять единственный каталог через `/api/library/*`. Маршрут `/api/nd/*` MUST быть удалён и возвращать `404`. Поля `nd_song_id`, `nd_album_id`, `nd_artist_id`, `nd_playlist_id`, `nd_username`, `nd_password_enc` MUST отсутствовать в моделях, схемах и ответах API.

#### Scenario: Старый endpoint
- **WHEN** клиент вызывает `/api/nd/home` или другой `/api/nd/*`
- **THEN** Hub возвращает `404`

#### Scenario: Старое поле
- **WHEN** клиент читает трек, альбом, плейлист или пользователя
- **THEN** в ответе нет `nd_*`-полей
- **AND** идентификаторы — только `uid`

#### Scenario: Миграция схемы
- **WHEN** применяется миграция удаления ND-полей
- **THEN** соответствующие колонки удаляются
- **AND** откат восстанавливает их пустыми

## REMOVED Requirements

### Requirement: Прокси библиотеки Navidrome

**Reason**: Navidrome удалён; переходный слой `/api/nd/*` больше не нужен, PWA работает через `/api/library/*`.
**Migration**: Все клиенты используют `/api/library/*` и `lib:<uid>`; старые сборки получают `404` и должны обновиться.
