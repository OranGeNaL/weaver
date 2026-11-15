## 1. Инвентаризация

- [ ] 1.1 Найти все записи `favorite_subjects.target_type` в `hub/app` и `hub/tests` (`grep -rn "target_type"`) и подтвердить, что значения берутся только из `TargetType`/`FavoriteTarget`. Проверка: список мест и вывод в отчёте.
- [ ] 1.2 Зафиксировать текущий head (`uv run alembic heads` → `j0f7e3c5d9a4`) и набор `ck_*` в `models.metadata` (8 ограничений). Проверка: вывод команды.

## 2. Схема и модели

- [ ] 2.1 `hub/app/models.py`: `CheckConstraint("target_type IN ('track', 'album', 'artist')", name="ck_favorite_subjects_target_type")` в `FavoriteSubject.__table_args__` рядом с `UniqueConstraint`. Проверка: `uv run pyright`.
- [ ] 2.2 Alembic-миграция `favorite subjects target type check`: `down_revision = "j0f7e3c5d9a4"`, `create_check_constraint` в `upgrade`, `drop_constraint` в `downgrade` (только это ограничение). Проверка: `uv run alembic upgrade head`, `uv run alembic downgrade -1`, снова `upgrade head` на чистой БД.
- [ ] 2.3 Проверить, что downgrade оставляет 8 прежних ограничений и удаляет только новое. Проверка: `SELECT conname FROM pg_constraint WHERE conname LIKE 'ck_%'` до/после `downgrade -1`.

## 3. Тесты

- [ ] 3.1 Добавить кейс `favorite_subjects.target_type` ← `TargetType` в параметризацию `hub/tests/test_enum_constraints.py` (каждое значение принимается на INSERT и UPDATE, чужое — `IntegrityError`, строка не сохраняется). Проверка: `uv run pytest -q tests/test_enum_constraints.py`.
- [ ] 3.2 Тест паритета моделей и миграций: `{имя: выражение}` `CheckConstraint` из `models.metadata` совпадает с `pg_get_constraintdef` в БД; имена — строго, выражения — после нормализации; при расхождении сообщение называет таблицу и имя. Проверка: тест зелёный на текущей схеме.
- [ ] 3.3 Проверить, что тест паритета ловит регресс: временно удалить `create_check_constraint` из миграции (в рабочей копии) и увидеть падение; откатить. Проверка: падение подтверждено в отчёте.
- [ ] 3.4 Валидные пути записи не изменились: существующие тесты `favorites` (♥) и `insights`/библиотеки, пишущие `favorite_subjects`, зелёные. Проверка: `uv run pytest -q tests/test_favorites.py tests/test_insights.py`.

## 4. Проверка

- [ ] 4.1 `TEST_DATABASE_URL=... uv run pytest -q -n 4` на тестовом Postgres: зелёный; число `passed`/`skipped` совпадает с baseline + новые кейсы. Проверка: вывод команды.
- [ ] 4.2 `cd hub && uv run pyright` → 0 ошибок; `uv run ruff check` и `uv run ruff format --check` → без ошибок. Проверка: вывод команд.
- [ ] 4.3 Прогнать `council-validate` по change'у и приложить меморандум с вердиктом; блокеры исправить до отметки задач выполненными.
