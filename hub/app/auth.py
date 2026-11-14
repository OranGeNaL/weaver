"""Local accounts: sign-in by argon2id password, Hub-issued tokens in HttpOnly cookies, per-user Navidrome clients.

The Hub is the source of truth for accounts since stage 4: `POST /api/auth/login` never asks Navidrome. The ND
client of a user is only a transitional proxy channel for `/api/nd/*` and stays empty for accounts created now
(`user_navidrome` must not be called without credentials).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import tokens
from app.config import get_settings
from app.db import get_session, session_scope
from app.library.navidrome import NavidromeClient
from app.models import LoginAttempt, RefreshToken, User, utcnow
from app.passwords import verify_password
from app.security import decrypt

log = logging.getLogger(__name__)

COOKIE = tokens.ACCESS_COOKIE  # `hm_session` kept its name: the PWA's cookie contract does not change
SEEN_EVERY = timedelta(minutes=5)  # how often a request refreshes User.last_seen_at (admin's user list)

# local attempt budget (no Navidrome/Subsonic budget is involved any more), stored in Postgres; per name only
PASSWORD_WINDOW_S = 30
PASSWORD_USER_LIMIT = 3

# the only endpoints a user with `must_change_password` may call
PASSWORD_CHANGE_ALLOWED = (
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/auth/password",
    "/api/auth/bootstrap",
    "/api/auth/options",
)


@dataclass(frozen=True)
class AuthResult:
    """A signed-in user: the pair of tokens and the device (session) it belongs to."""

    user: User
    access: str
    refresh: str
    device_id: str
    family_id: str


def _cookies_secure(request: Request) -> bool:
    settings = get_settings()
    if settings.public_hub_url.startswith("https://"):
        return True
    return request.url.scheme == "https"


def set_auth_cookies(
    request: Request, response: Response, access: str, refresh: str, device_id: str, family_id: str
) -> None:
    """Access in `hm_session`, refresh in `hm_refresh` (path `/api/auth`), device in `hm_device`: all HttpOnly."""
    secure = _cookies_secure(request)
    response.set_cookie(
        tokens.ACCESS_COOKIE,
        access,
        max_age=int(tokens.ACCESS_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        tokens.REFRESH_COOKIE,
        refresh,
        max_age=int(tokens.REFRESH_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=secure,
        path=tokens.REFRESH_PATH,
    )
    response.set_cookie(
        tokens.DEVICE_COOKIE,
        device_id,
        max_age=int(tokens.REFRESH_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=secure,
        path=tokens.REFRESH_PATH,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(tokens.ACCESS_COOKIE, path="/")
    response.delete_cookie(tokens.REFRESH_COOKIE, path=tokens.REFRESH_PATH)
    response.delete_cookie(tokens.DEVICE_COOKIE, path=tokens.REFRESH_PATH)
    # the pre-stage-4 session cookie had these attributes; delete both spellings
    response.delete_cookie(tokens.ACCESS_COOKIE, path="/", httponly=True, samesite="lax")


def _attempt_key(name: str) -> str:
    """The limiter key: the name as typed, folded and bounded to the column width."""
    return name.strip().lower()[:128]


async def check_rate(s: AsyncSession, name: str) -> None:
    """429 with Retry-After while the failures of this name fill the window.

    The budget is per name: three wrong passwords of one account never lock anybody else out, and there is no
    server-wide counter that a stranger could fill to block every sign-in.
    """
    key = _attempt_key(name)
    since = utcnow() - timedelta(seconds=PASSWORD_WINDOW_S)
    attempts, oldest = (
        await s.execute(
            select(func.count(LoginAttempt.id), func.min(LoginAttempt.attempted_at)).where(
                LoginAttempt.username == key, LoginAttempt.attempted_at >= since
            )
        )
    ).one()
    if attempts < PASSWORD_USER_LIMIT or oldest is None:
        return
    retry_after = max(1, PASSWORD_WINDOW_S - int((utcnow() - oldest).total_seconds()))
    raise HTTPException(
        429,
        "Слишком много попыток входа. Попробуйте позже.",
        headers={"Retry-After": str(retry_after)},
    )


async def record_failure(name: str) -> None:
    """The attempt is stored (and old ones are pruned) in its own transaction: the failed login rolls back."""
    async with session_scope() as w:
        w.add(LoginAttempt(username=_attempt_key(name)))
        await w.execute(
            delete(LoginAttempt).where(LoginAttempt.attempted_at < utcnow() - timedelta(seconds=PASSWORD_WINDOW_S * 4))
        )


def _device_id(request: Request) -> str:
    return request.cookies.get(tokens.DEVICE_COOKIE) or tokens.new_device_id()


async def find_user_for_login(s: AsyncSession, login: str) -> User | None:
    """Sign-in takes the name, or the confirmed email the account registered with."""
    login = login.strip()
    if not login:
        return None
    by_name = (await s.execute(select(User).where(func.lower(User.username) == login.lower()))).scalar_one_or_none()
    if by_name is not None or "@" not in login:
        return by_name
    return (await s.execute(select(User).where(User.email == login.lower()))).scalar_one_or_none()


async def password_login(s: AsyncSession, login: str, password: str, device_id: str) -> AuthResult:
    """Checks the local password (with the Postgres attempt limiter) and issues a pair of tokens."""
    name = _attempt_key(login)
    await check_rate(s, name)
    user = await find_user_for_login(s, login)
    if user is None or not verify_password(user.password_hash, password):
        await record_failure(name)
        raise HTTPException(401, "Неверный логин или пароль")
    user.last_seen_at = utcnow()
    pair = await tokens.issue_pair(s, user, device_id)
    log.info("signed in %s", user.username, extra={"user_id": user.id})
    return AuthResult(
        user=user, access=pair.access, refresh=pair.refresh, device_id=device_id, family_id=pair.family_id
    )


async def sign_in_user(s: AsyncSession, user: User, device_id: str) -> AuthResult:
    """A session for an already authenticated identity (registration, OIDC, password change)."""
    user.last_seen_at = utcnow()
    pair = await tokens.issue_pair(s, user, device_id)
    return AuthResult(
        user=user, access=pair.access, refresh=pair.refresh, device_id=device_id, family_id=pair.family_id
    )


async def refresh_session(s: AsyncSession, refresh_token: str | None, device_id: str) -> AuthResult:
    """Rotates the refresh token of the request (reuse outside the window revokes the session)."""
    if not refresh_token:
        raise HTTPException(401, "Требуется вход")
    try:
        pair, user = await tokens.rotate(s, refresh_token, device_id)
    except tokens.RefreshError as e:
        raise HTTPException(401, e.detail) from e
    return AuthResult(
        user=user, access=pair.access, refresh=pair.refresh, device_id=device_id, family_id=pair.family_id
    )


async def logout(s: AsyncSession, request: Request, response: Response) -> None:
    """Revokes the session of this device and clears the cookies."""
    refresh_token = request.cookies.get(tokens.REFRESH_COOKIE)
    family = await tokens.current_family(s, refresh_token)
    if family:
        await tokens.revoke_family(s, family)
    clear_auth_cookies(response)


async def current_user(request: Request, s: AsyncSession = Depends(get_session, scope="function")) -> User:
    """The signed-in user from the access JWT; `must_change_password` gates everything but the password change."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Требуется вход")
    try:
        claims = tokens.decode_access(token)
    except tokens.TokenError as e:
        raise HTTPException(401, "Сессия истекла, войдите снова") from e
    user = await s.get(User, int(claims["sub"]))
    if user is None:
        raise HTTPException(401, "Требуется вход")
    now = utcnow()
    if user.last_seen_at is None or now - user.last_seen_at > SEEN_EVERY:
        user.last_seen_at = now
    if user.must_change_password and not request.url.path.startswith(PASSWORD_CHANGE_ALLOWED):
        raise HTTPException(403, "Сначала смените пароль")
    # Provider resolution may take minutes and itself needs DB connections. Release the
    # authentication transaction before entering the endpoint, retaining the loaded user
    # in the shared session (expire_on_commit=False) for subsequent account updates.
    await s.commit()
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    """Administrative endpoints: the role is read from the database, never trusted from the JWT alone."""
    if not user.is_admin:
        raise HTTPException(403, "Только для администратора")
    return user


def user_navidrome(user: User) -> NavidromeClient:
    """Navidrome client acting as this user (transitional ND proxy). The Hub never calls it without credentials."""
    if not user.nd_username or not user.nd_password_enc:
        raise HTTPException(409, "Учётная запись Navidrome для этого пользователя не привязана")
    return NavidromeClient(user.nd_username, decrypt(user.nd_password_enc))


def has_navidrome(user: User) -> bool:
    """Whether the transitional `/api/nd/*` proxy may talk to Navidrome as this user."""
    return bool(user.nd_username and user.nd_password_enc)


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(admin_user)]
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]

__all__ = [
    "AuthResult",
    "CurrentUser",
    "RefreshToken",
    "admin_user",
    "check_rate",
    "clear_auth_cookies",
    "current_family",
    "find_user_for_login",
    "has_navidrome",
    "login",
    "logout",
    "password_login",
    "refresh_session",
    "set_auth_cookies",
    "sign_in_user",
    "user_navidrome",
]

# backwards-compatible aliases used across the codebase and tests
login = password_login


async def current_family(s: AsyncSession, request: Request) -> str | None:
    return await tokens.current_family(s, request.cookies.get(tokens.REFRESH_COOKIE))
