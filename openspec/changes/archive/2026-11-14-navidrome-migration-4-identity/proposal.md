## Why

Зависит от `navidrome-migration-0-governance` (правила `config.yaml`), `navidrome-migration-1-postgres` и `navidrome-migration-3-catalog`.

Сейчас Navidrome — источник истины для пользователей и паролей: Hub проверяет вход через Subsonic, хранит пароль шифрованным и создаёт учётки через админский API ND. После отказа от ND Hub должен сам владеть учётками, выдавать свои токены и оставаться совместимым с будущим OIDC-входом.

## What Changes

- **BREAKING** Вход проверяется по локальной учётной записи Hub (argon2), а не через Navidrome.
- Hub выдаёт собственные JWT: короткоживущий access и rotating refresh; PWA получает их в HttpOnly cookie.
- Добавить отзыв сессий и список устройств; refresh-токены хранятся хешем.
- Внешние OIDC-провайдеры (Google и generic) используются только для входа и привязки к локальной учётке (`iss` + `sub`); Hub не становится OIDC Provider.
- Добавить bootstrap администратора при первом старте без дефолтных `admin/admin`.
- Регистрация по приглашению и email создаёт локального пользователя, а не пользователя Navidrome.
- Убрать хранение пароля Navidrome и зависимость от его лимитов входа.
- Админский список пользователей, `last_seen_at` и счётчики строятся по БД Hub.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `auth`: вход, сессии/токены, приглашения, email, Google/OIDC, лимиты, админ-доступ и защита секретов переходят на локальные учётки и Hub-issued JWT.

## Impact

- Код: `hub/app/auth.py`, `hub/app/accounts.py`, `hub/app/google_auth.py`, `hub/app/api/auth.py`, `hub/app/api/admin.py`, `hub/app/security.py`, `hub/app/models.py`, `hub/app/config.py`; `web/src/api/client.ts`, `types.ts`, `pages/Login.tsx`, `pages/More.tsx`.
- БД: `users.username`/`password_hash`/`must_change_password`; `refresh_tokens`, `oauth_links`, `login_attempts`; `nd_username`/`nd_password_enc` остаются nullable без записи (drop — этап 7); `sessions` заменяется; миграция обязательна.
- API: `POST /api/auth/login`, `/refresh`, `/logout`, `/api/auth/oidc/*`, `/api/admin/users`; поле пароля Navidrome в ответе Google удаляется.
- Клиенты: PWA сохраняет cookie-поток; мобильные клиенты вне серии; будущий OIDC — через RP.
- Проверка: `pytest` (вход, refresh, отзыв, OIDC-линковка, bootstrap), `ruff`.
- Обратная совместимость: старые данные ND-пользователей отсутствуют; cookie-контракт PWA сохраняется.
