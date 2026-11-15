## Context

См. `proposal.md` — Why. Текущее состояние, важное для выбора решения:

- `app/catalog/models.py:65` — `CatalogAlbum` (Pydantic-модель домена) с полем
  `tracks: list[CatalogTrack] | None`; `CatalogTrack` (`:42`) — базовая доменная модель трека.
- `app/api/schemas.py:394` — `TrackOut(CatalogTrack)` добавляет поля принадлежности библиотеке;
  `:402` — `AlbumOut(CatalogAlbum)` добавляет `owned_status`/`owned_count` и **сужает** `tracks` до
  `list[TrackOut] | None`. `list` инвариантен, поэтому pyright требует подавление (`:405`).
- `AlbumOut` собирается в двух местах: `app/library/owned.py:193-196` (`AlbumOut(**d)`, где `d` — словарь
  из БД) и отдаётся как `response_model` в `app/api/catalog.py:72`.
- `CatalogAlbum` используется широко: каталоги (`deezer.py`, `hitmoz_catalog.py`), `ArtistPage`, тесты.
  Любая параметризация задевает все эти места.
- Гейт типов: `hub/pyrightconfig.json` — `include: ["app", "tests"]`, `typeCheckingMode: standard`,
  исключены только `__pycache__` и `app/static`. Подавлений в `app/**` ровно одно.

## Goals / Non-Goals

**Goals:**
- Ноль подавлений `pyright` в `app/**` при нуле ошибок: гейт снова означает «код типово чист», а не
  «подавления на месте».
- Форма ответа API (`AlbumOut`, включая поля `TrackOut` внутри `tracks`) и OpenAPI не меняются.
- Минимальная правка домена: `CatalogAlbum` не должен приобрести знание об API-слое (`TrackOut`).

**Non-Goals:**
- Не переписывать `TrackOut`/`LibAlbumOut`/`LibTrackOut` и не унифицировать схемы ответов.
- Не менять поведение эндпоинтов каталога и библиотеки, БД и фоновые задачи.
- Не вводить глобальные `# pyright: reportIncompatibleVariableOverride=false` и не расширять `exclude`.
- Не трогать `tests` (там ноль ошибок после `td-test-typing`).

## Decisions

### 1. Параметризовать доменную модель: `CatalogAlbum[TTrack]` (выбрано)

`TypeVar("TTrack", bound=CatalogTrack)`, `class CatalogAlbum(BaseModel, Generic[TTrack])`,
поле `tracks: list[TTrack] | None = None`. Специализации: `CatalogAlbum[CatalogTrack]` в каталогах и
`ArtistPage`, `AlbumOut(CatalogAlbum[TrackOut])` в API.

Почему: устраняет причину ошибки (инвариантность `list` при сужении типа элемента), а не подавляет её;
`AlbumOut` по-прежнему наследует доменную модель, поля `CatalogAlbum` не дублируются; Pydantic v2
поддерживает generic-модели, `CatalogAlbum[CatalogTrack]` сохраняет валидацию и сериализацию.

Цена: нужно обновить аннотации во всех местах, где встречается `CatalogAlbum` (каталоги, `ArtistPage`,
`AlbumOut`, тесты); `CatalogAlbum.model_validate(...)` и `run("album", CatalogAlbum, ...)` в `deezer.py`
должны получить специализацию.

### 2. Альтернатива: композиция в `AlbumOut` (отклонена)

`class AlbumOut(BaseModel)` с явным копированием полей `CatalogAlbum` либо с вложенным `catalog: CatalogAlbum`
и своим `tracks`.

Почему отклонена: копирование ~15 полей создаёт второй источник истины (ровно тот класс долга, который
устраняется); вложенный объект меняет форму ответа и ломает клиентов. Оставляем как запасной вариант, если
параметризация окажется несовместима с Pydantic/FastAPI в этом проекте.

### 3. Альтернатива: `tracks: list[CatalogTrack] | None` в `AlbumOut` (отклонена)

Тип сходится без подавления, но OpenAPI и `response_model` перестают объявлять поля `TrackOut`
(`owned`, `library_track_id`, `uid`, `downloading`, `star_pending`): PWA получает эти поля в рантайме, а
контракт в схеме их теряет. Это тихая деградация документированного ответа — недопустимо.

### 4. Гейт «0 подавлений» проверяется тестом

Добавить в `hub/tests` тест, который читает `app/**/*.py` и падает на `# pyright: ignore` и
`# pyright: <rule>=false`; допустимое исключение — только явный allowlist с комментарием-обоснованием
(на момент change'а список пуст). Без этого новый `ignore` вернётся молча.

## Risks / Trade-offs

- [Параметризация `CatalogAlbum` задевает весь каталог] → правки механические; полный `pytest` и `pyright`
  ловят пропущенные специализации, тесты каталогов (deezer/hitmoz/ytmusic) проходят на реальных фикстурах.
- [Pydantic-генерик может изменить JSON-схему] → снимок OpenAPI и ответа `GET /api/catalog/album/{ref}` до/после
  сравниваются в задачах; расхождение — блокер.
- [Тест на подавления может стать формальностью] → allowlist пуст и снабжён требованием обоснования в
  комментарии; появление записи в allowlist — отдельное решение в рамках нового change'а.
- [Pydantic сериализует `TrackOut` как `CatalogTrack` при `AlbumOut(CatalogAlbum[TrackOut])`?] → проверяется
  снимком ответа: поля `TrackOut` MUST присутствовать.

## Migration Plan

Схема БД, API и клиенты не меняются — миграция не требуется. Откат: вернуть подавление и предыдущие
аннотации (обратная совместимость полная, поведение не затрагивается).
