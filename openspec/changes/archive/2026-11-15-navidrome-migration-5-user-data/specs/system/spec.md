## ADDED Requirements

### Requirement: Capability избранного Hub

Hub SHALL сообщать в `/healthz.capabilities` возможность `hub_liked_v1`, означающую, что `/api/starred` и `/api/library/liked` обслуживаются Hub и содержат `track_uids`.

#### Scenario: Новый клиент
- **WHEN** клиент видит `hub_liked_v1`
- **THEN** он MAY использовать `track_uids` и `/api/library/liked`
- **AND** старый клиент продолжает использовать `song_ids`

#### Scenario: Старый клиент
- **WHEN** клиент не знает `hub_liked_v1`
- **THEN** он работает с прежними полями `/api/starred`
