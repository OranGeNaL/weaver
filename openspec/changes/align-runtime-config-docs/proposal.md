## Why

Документация конфигурации расходится с фактическим поведением:

- комментарий `backup_hour` говорит «local time», а `housekeeping` использует время контейнера (обычно UTC);
- `config.example.yaml` не документирует настройки `MixesCfg`, которые реально поддерживаются;
- `.env.example` содержит мёртвые `LASTFM_SHARED_SECRET`, `LASTFM_APPLICATION_NAME`, `LASTFM_REGISTERED_TO` — код читает только `LASTFM_API_KEY`.

## What Changes

- Исправить комментарий `backup_hour` на «время контейнера (обычно UTC)».
- Добавить в `config.example.yaml` документированный блок настроек микса: `size`, `reserve`, `default_hour`, `seeds`, `max_per_artist`, `min_duration_s`, `max_duration_s`, `weights`, `taste_model`, `taste_shadow`.
- Удалить из `.env.example` неиспользуемые `LASTFM_*` ключи, оставив `LASTFM_API_KEY`.
- Не менять код и значения по умолчанию.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет. Это изменение документации и примеров конфигурации, поведение системы не меняется.

## Impact

- `config.example.yaml`
- `.env.example`
- возможно `README.md`, если он цитирует старые ключи
