## ADDED Requirements

### Requirement: Допустимые значения перечислимых полей

Hub SHALL ограничивать на уровне Postgres значения перечислимых текстовых колонок первого набора — `imports.source`,
`imports.method`, `imports.mode`, `import_collections.kind`, `albums.release_type`, `mix_settings.mode`, `mixes.mode`,
`wave_sessions.mode` — то есть полей, на которые опираются Literal-типы API и домена. Наборы MUST совпадать с
соответствующими Literal; изменение набора MUST сопровождаться Alembic-миграцией. Ограничения MUST иметь имена
`ck_<таблица>_<колонка>` и MAY допускать `NULL` для nullable-полей.

Другие касты "БД → Literal" вне этого перечня (например `favorite_subjects.target_type`,
`favorite_events.target_type`) остаются на соглашении: их ограничение — отдельный change.

#### Scenario: Допустимое значение
- **WHEN** приложение записывает значение из набора
- **THEN** запись сохраняется

#### Scenario: Недопустимое значение
- **WHEN** в колонку пытаются записать значение вне набора
- **THEN** Postgres отклоняет запись ошибкой ограничения
- **AND** строка не сохраняется

#### Scenario: Изменение набора
- **WHEN** набор допустимых значений меняется
- **THEN** change содержит Alembic-миграцию, обновляющую ограничение
- **AND** проверены `upgrade head` и откат

#### Scenario: Паритет с Literal
- **WHEN** выполняется тест паритета
- **THEN** каждое значение соответствующего Literal принимается БД
- **AND** заведомо чужое значение отвергается
