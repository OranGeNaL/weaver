## 1. Схема и конфигурация

- [ ] 1.1 Добавить `users.username`, `password_hash`, `must_change_password`; таблицы `refresh_tokens` (`family_id`, `device_id`, `replacement_enc`, `revoked_at`), `oauth_links`, `login_attempts`, `bootstrap_consumed_at`; **не удалять** `nd_username`/`nd_password_enc` — оставить nullable и перестать писать `nd_password_enc` (drop — этап 7); проверить `alembic upgrade head` и откат.
- [ ] 1.2 Добавить обязательные `JWT_SIGNING_KEY`, `HUB_SECRET_KEY`, настройки OIDC-провайдеров; проверить, что без ключей Hub не стартует.
- [ ] 1.3 Проверить, что `/api/auth/login` не обращается к `NAVIDROME_URL`; ожидается отсутствие вызовов в тестах.
- [ ] 1.4 Обновить `hub/tests/support.py` и фикстуры, которые создают `User(nd_username=…, nd_password_enc=…)`; проверить, что тесты входят локально, а не через ND.

## 2. Пароли и bootstrap

- [ ] 2.1 Реализовать argon2id-хеширование и проверку пароля; проверить unit-тесты хеша и неверного пароля.
- [ ] 2.2 Реализовать `POST /api/auth/bootstrap` (env/`ADMIN_PASSWORD_HASH`/одноразовый токен с `bootstrap_consumed_at` и лимитером), проверку политики пароля и `must_change_password`; проверить первый старт, повторный старт, слабый пароль и отсутствие `admin/admin`.
- [ ] 2.3 Реализовать `POST /api/auth/password`, `MeOut.must_change_password`, `403` до смены и CLI-сброс; проверить, что после смены/сброса старые refresh-токены (кроме текущей семьи) отозваны.

## 3. Токены

- [ ] 3.1 Реализовать выдачу access JWT и refresh-токена с хранением хеша и `family_id`; проверить тесты входа и защищённого endpoint.
- [ ] 3.2 Реализовать `POST /api/auth/refresh` с ротацией, `replacement_enc`, cookie `hm_device`, окном идемпотентности и reuse detection; проверить, что повтор старого refresh вне окна отзывает семью, а параллельный — нет.
- [ ] 3.3 Реализовать `POST /api/auth/logout`, `GET /api/auth/sessions` и `DELETE /api/auth/sessions/{id}`, где `{id}` — `family_id`/устройство; проверить, что все refresh семьи перестают действовать.
- [ ] 3.5 Реализовать в PWA refresh-on-401 (single-flight), список провайдеров из `/api/auth/options`, OIDC redirect/callback/link, экран смены пароля; файлы `web/src/api/client.ts`, `types.ts`, `pages/Login.tsx`, `pages/More.tsx`; проверить typecheck/build.
- [ ] 3.4 Проверить cookie-атрибуты (`HttpOnly`, `Secure` при HTTPS, `SameSite`, path) и совместимость с PWA.

## 4. Регистрация и email

- [ ] 4.1 Перевести регистрацию по приглашению на локального пользователя; проверить успешный вход, использованный код и занятое имя.
- [ ] 4.2 Перевести регистрацию по email на локального пользователя; проверить лимиты, неверный код и успешную регистрацию.
- [ ] 4.4 Зафиксировать переходный период stage 4: пользователь без ND-учётки получает ND-совместимый ответ из Hub-каталога, без `409`; проверить тест.
- [ ] 4.3 Убедиться, что `NavidromeAdmin.create_user` больше не вызывается; проверить `rg "NavidromeAdmin"` и тесты аккаунтов.

## 5. OIDC

- [ ] 5.1 Реализовать generic OIDC RP (discovery, Authorization Code + PKCE, `state`/`nonce`) и сценарии: вход, привязка, действующий инвайт, конфликт `iss`+`sub`, отвязка, ошибка discovery/`state`; проверить на фейковом провайдере.
- [ ] 5.2 Перевести Google на OIDC-провайдер и убрать пароль Navidrome из ответа; проверить контракт `MeOut`.
- [ ] 5.3 Реализовать правила линковки (MUST NOT автопривязка по email, конфликт `iss`+`sub`, отвязка), путь `POST /api/auth/oidc/link/{provider}`; проверить тесты `409`.
- [ ] 5.4 Проверить, что Hub не отдаёт OIDC discovery/authorize/token для сторонних приложений.

## 6. Админ и безопасность

- [ ] 6.1 Перевести `/api/admin/users` на локальных пользователей и `last_seen_at`; проверить, что ND-пользователи отсутствуют.
- [ ] 6.2 Проверить лимиты попыток входа (хранение в Postgres, переживает рестарт) и формат ошибок `401/403/409/429`; ожидается русский `detail`.
- [ ] 6.3 Проверить, что пароли, токены и OIDC-секреты не попадают в логи; проверить `rg` по логам в тестах.

## 7. Проверка

- [ ] 7.1 Прогнать `pytest` (вход, refresh, отзыв, регистрация, OIDC, bootstrap, смена пароля, админ); ожидается зелёный прогон.
- [ ] 7.2 Прогнать `ruff check`, `ruff format --check`, `npm run typecheck` и `npm run build`; ожидается без ошибок.
- [ ] 7.3 Проверить `alembic upgrade head` и `alembic downgrade -1` для миграции идентичности.
- [ ] 7.4 Проверить вручную вход в PWA, refresh после истечения access и выход.
- [ ] 7.5 Обновить README/ARCHITECTURE и `hub/API.md` в части локальных учёток и OIDC.
- [ ] 7.6 Обновить `Purpose` в `openspec/specs/auth/spec.md` при архивации (сейчас там Navidrome как источник истины).
