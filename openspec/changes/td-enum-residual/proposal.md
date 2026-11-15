## Why

`td-enum-check-constraints` закрыл первый набор колонок, но оставил два разрыва в том же инварианте:

1. `favorite_subjects.target_type` (`app/models.py:618`, `String(16)`) читается как Literal:
   `target_type=cast(TargetType, state.target_type)` (`app/favorites.py:387`, `TargetType = Literal["track",
   "album", "artist"]`). Это ровно тот failure mode, из-за которого заводился прошлый change: невалидное
   значение из старой строки всплывает `ResponseValidationError` на чтении, а не отказом на записи.
2. Ничто не проверяет, что `CheckConstraint` в `models.__table_args__` совпадают с ограничениями в миграциях.
   `alembic check` в репозитории красный по pre-existing причинам (trgm-индексы и inline-индексы
   `ix_download_items_*`) и CHECK-ограничения не сравнивает вовсе, поэтому дрейф «модель добавила
   констрейнт, миграция — нет» проходит молча. После архивации прошлого change'а этот разрыв зафиксирован
   в его меморандуме как P2.

## What Changes

- Добавить `CheckConstraint` `ck_favorite_subjects_target_type` (`target_type IN ('track','album','artist')`)
  в модель `FavoriteSubject.__table_args__` и Alembic-миграцию, создающую его (`create_check_constraint`),
  с симметричным `drop_constraint` в `downgrade`.
- Расширить паритетный тест `hub/tests/test_enum_constraints.py`: новое значение набора принимается,
  чужое — `IntegrityError`, набор в `pg_constraint` совпадает с `get_args(TargetType)`.
- Добавить тест паритета **модели и миграций**: для каждой `CheckConstraint` из `models.metadata` в схеме
  БД после `alembic upgrade head` существует ограничение с тем же именем и эквивалентным выражением, и
  наоборот — ни одно `ck_*` из БД не потеряно в моделях. Расхождение — падение теста с указанием пары
  «таблица.ограничение».
- Не менять API, формат ответов и валидные пути записи: `FavoriteSubject` пишется из Pydantic-схем, где
  `TargetType` уже провалидирован.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `persistence`: требование «Допустимые значения перечислимых полей» расширяется колонкой
  `favorite_subjects.target_type` и гарантией «модели и миграции совпадают по CHECK-ограничениям»
  (проверяется тестом, а не `alembic check`).

## Impact

- БД: новая Alembic-миграция (одно ограничение); на пустой базе `upgrade` проходит, на БД с невалидными
  строками — ожидаемый отказ (рабочих инстансов нет, схема поднимается с нуля).
- Код: `hub/app/models.py` (`FavoriteSubject.__table_args__`), миграция, `hub/tests/test_enum_constraints.py`.
- Поведение записи: невалидный `target_type` теперь отвергается БД — объявленное ужесточение контракта
  хранения, API и клиенты не меняются.
- Проверка: `alembic upgrade head` / `downgrade -1` / `upgrade head`, `pytest`, `pyright`, `ruff`.
