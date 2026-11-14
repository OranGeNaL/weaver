"""The first administrator: from the environment at startup or by the one-time `ADMIN_BOOTSTRAP_TOKEN`.

There are no default credentials (`admin/admin` never exists). Bootstrapping is idempotent: as soon as an
administrator exists nothing happens. A password from the environment is treated as chosen by the operator (no
forced change); the one-time token path and the CLI reset set `must_change_password`.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.accounts import USERNAME_RE
from app.config import get_settings
from app.db import session_scope
from app.models import BootstrapState, User, utcnow
from app.passwords import check_password_policy, hash_password

log = logging.getLogger(__name__)

BOOTSTRAP_ROW = 1
# the limiter key of wrong bootstrap tokens: "@" cannot pass USERNAME_RE, so it cannot collide with an account
BOOTSTRAP_ATTEMPT = "@bootstrap"

INSTRUCTION = (
    "Администратор ещё не создан. Задайте ADMIN_USERNAME и ADMIN_PASSWORD (или ADMIN_PASSWORD_HASH) "
    "в .env и перезапустите Hub, либо задайте ADMIN_BOOTSTRAP_TOKEN и вызовите POST /api/auth/bootstrap."
)


async def admin_exists(s: AsyncSession) -> bool:
    return (await s.execute(select(User.id).where(User.is_admin.is_(True)).limit(1))).first() is not None


async def create_admin(
    s: AsyncSession, username: str, *, password: str | None = None, password_hash: str | None = None
) -> User:
    """A new administrator row: a plain password is hashed with argon2id, a given hash is taken as is."""
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Имя: от 2 до 32 букв или цифр, можно «.», «_», «-», без пробелов")
    if password_hash:
        stored = password_hash
    else:
        check_password_policy(username, password or "")
        stored = hash_password(password or "")
    user = User(username=username, password_hash=stored, is_admin=True, must_change_password=False)
    s.add(user)
    await s.flush()
    log.info("administrator %s created", username, extra={"user_id": user.id})
    return user


async def bootstrap_from_env() -> None:
    """At startup: creates the first administrator from `ADMIN_*`, or logs how to make one. Never fails the boot."""
    settings = get_settings()
    async with session_scope() as s:
        if await admin_exists(s):
            return
        if not settings.admin_username or not (settings.admin_password or settings.admin_password_hash):
            log.warning(INSTRUCTION)
            return
        try:
            await create_admin(
                s,
                settings.admin_username,
                password=settings.admin_password or None,
                password_hash=settings.admin_password_hash or None,
            )
        except HTTPException as e:
            log.error("администратор не создан: %s", e.detail)


async def bootstrap_with_token(
    s: AsyncSession, token: str, username: str, password: str, device_id: str
) -> auth.AuthResult:
    """`POST /api/auth/bootstrap`: the one-time token creates the administrator exactly once.

    Wrong tokens are counted by the login limiter (Postgres), so guessing is bounded before the administrator exists.
    """
    settings = get_settings()
    if not settings.admin_bootstrap_token:
        raise HTTPException(404, "Bootstrap администратора на этом сервере не настроен")
    await auth.check_rate(s, BOOTSTRAP_ATTEMPT)
    if await admin_exists(s):
        raise HTTPException(409, "Администратор уже создан")
    if not hmac.compare_digest(token.strip().encode("utf-8"), settings.admin_bootstrap_token.encode("utf-8")):
        await auth.record_failure(BOOTSTRAP_ATTEMPT)
        raise HTTPException(401, "Неверный bootstrap-токен")
    state = await s.get(BootstrapState, BOOTSTRAP_ROW)
    if state is None:
        state = BootstrapState(id=BOOTSTRAP_ROW)
        s.add(state)
        await s.flush()
    if state.consumed_at is not None:
        raise HTTPException(409, "Bootstrap-токен уже использован")
    user = await create_admin(s, username, password=password)
    user.must_change_password = True
    state.consumed_at = utcnow()
    await s.flush()
    return await auth.sign_in_user(s, user, device_id)


async def reset_password(username: str, password: str) -> User:
    """The CLI recovery: a new password and `must_change_password`; every session of the user is revoked."""
    from app import tokens

    async with session_scope() as s:
        user = (await s.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if user is None:
            raise HTTPException(404, f"Нет пользователя «{username}»")
        check_password_policy(user.username, password)
        user.password_hash = hash_password(password)
        user.must_change_password = True
        await tokens.revoke_other_sessions(s, user.id, None)
        await s.flush()
        return user
