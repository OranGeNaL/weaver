## Context

`UploadCommitIn` уже содержит `playlist_id`; PWA отправляет его вместе с выбранным альбомом и треками. `commit` создаёт альбом, импортирует файлы и запускает `nd_scan`, но `playlist_id` нигде не используется. В отличие от загрузок, у upload нет `DownloadItem`, поэтому существующий `PendingPlaylistAdd.download_item_id` напрямую не подходит.

## Goals / Non-Goals

**Goals:**

- Добавлять импортированные upload-треки в выбранный плейлист Navidrome.
- Сохранять порядок треков из `UploadCommitIn.tracks`.
- Не ломать текущий `commit` без `playlist_id`.
- Переживать задержку между импортом файла и появлением `nd_song_id` после скана.
- Не создавать дубликаты при повторной обработке.

**Non-Goals:**

- Менять UI загрузки или добавлять новые способы выбора плейлиста.
- Менять формат `UploadCommitIn`, кроме уже существующего `playlist_id`.
- Делать синхронное ожидание скана внутри HTTP-запроса.

## Decisions

1. Расширить `pending_playlist_adds`:
   - сделать `download_item_id` nullable;
   - добавить nullable `upload_id`;
   - добавить CHECK/инвариант: заполнен ровно один из `download_item_id` или `upload_id`.
2. При `commit` после успешного импорта каждого трека создавать `PendingPlaylistAdd` с `playlist_id`, `upload_id`, `position`.
3. Обработчик отложенных добавлений определяет `nd_song_id` через `Upload.track_id` → `Track.nd_song_id`; если скана ещё не было, оставляет запись до следующего прохода.
4. Использовать существующий `playlist_adds_loop` и `process_playlist_adds`, расширив их на upload-строки.
5. Если импорт трека завершился ошибкой, `PendingPlaylistAdd` не создаётся.

## Risks / Trade-offs

- Нужна Alembic-миграция: ослабление `download_item_id` и новый `upload_id`.
- Добавление ждёт `nd_scan`; при долгом скане пользователь увидит трек в библиотеке раньше, чем в плейлисте.
- Нужно аккуратно обрабатывать удалённый upload и уже добавленный трек, чтобы не создавать дубликаты.
