## ADDED Requirements

### Requirement: Capability каталога Hub

Hub SHALL сообщать в `/healthz.capabilities` возможность `hub_library_v1`, означающую, что каталог, обложки и библиотечное прослушивание доступны через `/api/library/*` и `/api/listen?ref=lib:<uid>`.

#### Scenario: Новый клиент
- **WHEN** клиент видит `hub_library_v1`
- **THEN** он MAY использовать `/api/library/*` и `lib:<uid>`
- **AND** не обращается к `/api/nd/*`

#### Scenario: Старый клиент
- **WHEN** клиент не знает `hub_library_v1`
- **THEN** он продолжает работать через прежние endpoint'ы до этапа 7
