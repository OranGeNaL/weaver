## 1. Базовая линия

- [ ] 1.1 Зафиксировать снимок: `curl`/тест `GET /api/catalog/album/{ref}` и OpenAPI (`/openapi.json`) на `master`; сохранить в отчёт (до правок). Проверка: снимок содержит поля `owned`, `library_track_id`, `uid`, `downloading`, `star_pending` в `tracks`.
- [ ] 1.2 Зафиксировать baseline гейтов: `cd hub && uv run pyright` (ожидается 1 подавление, 0 ошибок), `TEST_DATABASE_URL=... uv run pytest -q -n 4` → число `passed`/`skipped`; перечислить все места `CatalogAlbum` (`grep -rn "CatalogAlbum" hub/app hub/tests`).

## 2. Параметризация доменной модели

- [ ] 2.1 `hub/app/catalog/models.py`: ввести `TTrack = TypeVar("TTrack", bound=CatalogTrack)`, `CatalogAlbum(BaseModel, Generic[TTrack])`, поле `tracks: list[TTrack] | None = None`; `ArtistPage.albums: list[CatalogAlbum[CatalogTrack]]`. Проверка: `uv run pyright` показывает оставшиеся места специализации списком.
- [ ] 2.2 Обновить каталоги: `deezer.py` (`map_album`, `dedupe_and_sort`, `search_albums`, `run("album", ...)`), `hitmoz_catalog.py`, `ytmusic`-каталог (если использует) — специализировать `CatalogAlbum[CatalogTrack]`, сохранив `model_validate`. Проверка: `uv run pytest -q tests/test_deezer.py tests/test_hitmoz.py tests/test_catalog*.py`.
- [ ] 2.3 `hub/app/api/schemas.py`: `class AlbumOut(CatalogAlbum[TrackOut])`, удалить `# pyright: ignore[reportIncompatibleVariableOverride]`. Проверка: `uv run pyright` → 0 ошибок и 0 подавлений в `app/**`.

## 3. Контракт ответа

- [ ] 3.1 Тест формы ответа `GET /api/catalog/album/{ref}`: `tracks[*]` содержит `owned`, `library_track_id`, `uid`, `downloading`, `star_pending`; сравнить со снимком 1.1. Проверка: `uv run pytest -q tests/test_catalog_album.py` (или существующий тест каталога).
- [ ] 3.2 Проверить `app/library/owned.py:193-196` (`AlbumOut(**d)`): сборка из словаря по-прежнему валидирует `tracks` как `TrackOut`. Проверка: тест `owned`-альбома из библиотеки зелёный.
- [ ] 3.3 Сравнить OpenAPI-схемы `AlbumOut`/`TrackOut` до/после (1.1): полей не убавилось, типы не сузились. Проверка: diff снимков пуст (или объяснён).

## 4. Гейт «0 подавлений»

- [ ] 4.1 Тест `hub/tests/test_type_suppressions.py`: сканирует `hub/app/**/*.py`, падает на `# pyright: ignore` / `# pyright: <rule>=false`; allowlist пуст, запись в него требует комментария-обоснования. Проверка: тест зелёный на текущем дереве.
- [ ] 4.2 Убедиться, что тест действительно ловит регресс: временно добавить `# pyright: ignore` в произвольный файл `app/**`, увидеть падение, откатить. Проверка: падение подтверждено в отчёте.

## 5. Проверка

- [ ] 5.1 `cd hub && uv run pyright` (`include: ["app", "tests"]`) → 0 ошибок, 0 подавлений в `app/**`. Проверка: вывод команды.
- [ ] 5.2 `uv run ruff check` и `uv run ruff format --check` → без ошибок. Проверка: вывод команд.
- [ ] 5.3 `TEST_DATABASE_URL=... uv run pytest -q -n 4` на тестовом Postgres → зелёный; число `passed`/`skipped` совпадает с baseline 1.2 (кроме тестов, добавленных этим change'ем). Проверка: вывод команды и сравнение с 1.2.
- [ ] 5.4 Прогнать `council-validate` по change'у (два прохода read-only советников) и приложить меморандум с вердиктом; блокеры исправить до отметки задач выполненными.
