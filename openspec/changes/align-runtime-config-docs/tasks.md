## 1. config.example.yaml

- [ ] 1.1 Исправить комментарий `library.backup_hour` на время контейнера (UTC).
- [ ] 1.2 Добавить документированный блок `mixes` с реальными полями `MixesCfg`.
- [ ] 1.3 Проверить, что пример остаётся валидным YAML и совпадает с `AppConfig`.

## 2. .env.example

- [ ] 2.1 Удалить `LASTFM_SHARED_SECRET`, `LASTFM_APPLICATION_NAME`, `LASTFM_REGISTERED_TO`.
- [ ] 2.2 Оставить `LASTFM_API_KEY` и пояснение, что он необязателен.
- [ ] 2.3 Проверить README на ссылки на удалённые ключи.

## 3. Проверка

- [ ] 3.1 `docker compose config` с тестовым `.env` проходит.
- [ ] 3.2 `rg` не находит удалённые ключи в документации.
