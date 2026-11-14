"""Accounts: by invitation, by email, or through an external OIDC provider — always a local Hub account.

Stage 4: the Hub owns accounts. Registration creates `users.username`/`password_hash` and never a Navidrome user;
an OIDC identity is linked as `(issuer, subject)` in `oauth_links` and is never auto-linked by email.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import deque
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth, mailer
from app.config import get_settings
from app.db import session_scope
from app.google_auth import GoogleTokenError, verify_id_token
from app.models import EmailCode, Invite, OAuthLink, User, utcnow
from app.passwords import check_password_policy, hash_password

log = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: the code is read out and typed by hand
CODE_LEN = 8
# letters of any script, digits, "._-": the name is typed in the player too
USERNAME_RE = re.compile(r"^[\w.\-]{2,32}$")
MIN_PASSWORD = 6

# Wrong codes: the codes are long enough, this only keeps a guesser from trying for long
_FAIL_WINDOW_S = 600
_FAIL_LIMIT = 30
_failures: deque[float] = deque()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_RESEND_S = 60  # a new letter to the same address
EMAIL_HOURLY = 5  # letters to one address an hour
EMAIL_ATTEMPTS = 5  # wrong codes before the letter's code stops working

GOOGLE_ISSUER = "https://accounts.google.com"


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def normalize_code(code: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", code.upper())


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}" if len(code) == 8 else code


def invite_status(inv: Invite) -> str:
    if inv.used_at is not None:
        return "used"
    return "expired" if inv.expires_at < utcnow() else "active"


async def create_invite(s: AsyncSession, admin: User, note: str, days: int | None) -> Invite:
    days = days or get_settings().invite_days
    while True:
        code = new_code()
        if (await s.execute(select(Invite.id).where(Invite.code == code))).first() is None:
            break
    inv = Invite(code=code, note=note.strip(), created_by=admin.id, expires_at=utcnow() + timedelta(days=days))
    s.add(inv)
    await s.flush()
    return inv


def _check_rate() -> None:
    now = time.monotonic()
    while _failures and now - _failures[0] > _FAIL_WINDOW_S:
        _failures.popleft()
    if len(_failures) >= _FAIL_LIMIT:
        raise HTTPException(429, "Слишком много попыток. Подождите несколько минут")


def _fail(status: int, detail: str) -> HTTPException:
    _failures.append(time.monotonic())
    return HTTPException(status, detail)


def check_new_account(username: str, password: str) -> None:
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Имя: от 2 до 32 букв или цифр, можно «.», «_», «-», без пробелов")
    check_password_policy(username, password)


async def _check_name_free(s: AsyncSession, username: str) -> None:
    taken = await s.execute(select(User.id).where(func.lower(User.username) == username.lower()))
    if taken.first() is not None:
        raise HTTPException(409, "Это имя уже занято, выберите другое")


async def _create_account(
    s: AsyncSession,
    username: str,
    password: str | None,
    device_id: str,
    *,
    email: str | None = None,
    claims: dict[str, Any] | None = None,
    provider: str | None = None,
) -> auth.AuthResult:
    """A local Hub account (argon2id hash, an optional OIDC link) and a session for it."""
    # an OIDC-only account has no password at all until its owner sets one in the settings
    user = User(username=username, password_hash=hash_password(password) if password else None, email=email)
    s.add(user)
    await s.flush()
    if claims is not None:
        s.add(
            OAuthLink(
                user_id=user.id,
                provider=provider or "google",
                issuer=str(claims["iss"]),
                subject=str(claims["sub"]),
            )
        )
        if provider in (None, "google"):
            user.google_sub, user.google_email = str(claims["sub"]), claims.get("email")
    await s.flush()
    return await auth.sign_in_user(s, user, device_id)


async def _consume_invite(s: AsyncSession, code: str) -> Invite:
    norm = normalize_code(code)
    inv = (await s.execute(select(Invite).where(Invite.code == norm))).scalar_one_or_none() if norm else None
    if inv is None or invite_status(inv) != "active":
        raise _fail(400, "Код приглашения не подходит: проверьте его или попросите новый")
    # Taken first, within this transaction: a second registration with the same code waits for it and finds it used.
    res = await s.execute(
        update(Invite)
        .where(Invite.id == inv.id, Invite.used_at.is_(None))
        .values(used_at=utcnow())
        .returning(Invite.id)
    )
    if res.first() is None:
        raise _fail(400, "Код приглашения уже использован")
    return inv


async def register(
    s: AsyncSession,
    code: str,
    username: str,
    password: str,
    device_id: str,
    google: dict[str, Any] | None = None,
) -> auth.AuthResult:
    """Creates a local Hub account for an unused invite and signs in."""
    _check_rate()
    username = username.strip()
    check_new_account(username, password)
    inv = await _consume_invite(s, code)
    await _check_name_free(s, username)
    if google is not None:
        await _check_oidc_free(s, GOOGLE_ISSUER, str(google["sub"]))
    result = await _create_account(
        s, username, password, device_id, claims=google, provider="google" if google is not None else None
    )
    inv.used_by = result.user.id
    log.info("registered %s by invite %s", username, inv.id, extra={"user_id": result.user.id})
    return result


# --- registration by email ------------------------------------------------------------------


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _code_hash(email: str, code: str) -> str:
    key = get_settings().hub_secret_key.encode()
    return hmac.new(key, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _check_email_signup() -> None:
    if not mailer.configured():
        raise HTTPException(404, "Регистрация по почте на этом сервере не настроена")


async def _check_email_free(s: AsyncSession, email: str) -> None:
    if (await s.execute(select(User.id).where(User.email == email))).first() is not None:
        raise HTTPException(409, "С этой почтой аккаунт уже есть — войдите в него")


async def send_email_code(email: str) -> int:
    """Sends a registration code to the address; returns the seconds before another letter may be asked for.
    The letter goes out after the commit: SMTP takes seconds, the database is not held for them."""
    _check_email_signup()
    email = normalize_email(email)
    if len(email) > 320 or not EMAIL_RE.match(email):
        raise HTTPException(400, "Проверьте адрес почты")
    settings = get_settings()
    code = f"{secrets.randbelow(10**6):06d}"
    now = utcnow()
    async with session_scope() as s:
        await _check_email_free(s, email)
        recent = list(
            (
                await s.execute(
                    select(EmailCode.created_at).where(
                        EmailCode.email == email, EmailCode.created_at >= now - timedelta(hours=1)
                    )
                )
            ).scalars()
        )
        if recent and (wait := EMAIL_RESEND_S - int((now - max(recent)).total_seconds())) > 0:
            raise HTTPException(429, f"Письмо уже отправлено. Новое можно запросить через {wait} с")
        if len(recent) >= EMAIL_HOURLY:
            raise HTTPException(429, "Слишком много писем на этот адрес. Попробуйте через час")
        today = (
            await s.execute(select(func.count(EmailCode.id)).where(EmailCode.created_at >= now - timedelta(days=1)))
        ).scalar_one()
        if today >= settings.email_daily_limit:
            log.warning("email daily limit %s reached", settings.email_daily_limit)
            raise HTTPException(503, "Сегодня сервер уже отправил все письма. Попробуйте завтра")
        # only the latest letter counts
        await s.execute(
            update(EmailCode).where(EmailCode.email == email, EmailCode.used_at.is_(None)).values(used_at=now)
        )
        row = EmailCode(
            email=email,
            code_hash=_code_hash(email, code),
            expires_at=now + timedelta(minutes=settings.email_code_minutes),
        )
        s.add(row)
        await s.flush()
        row_id = row.id
    text = (
        f"Ваш код для регистрации в HomeMusic: {code}\n\n"
        f"Введите его на экране регистрации. Код действует {settings.email_code_minutes} минут.\n"
        "Если вы не регистрировались в HomeMusic, просто не обращайте внимания на это письмо.\n"
    )
    try:
        await mailer.send(email, f"Код HomeMusic: {code}", text)
    except mailer.MailError as e:
        async with session_scope() as s:  # not sent: asking again right away is fine
            await s.execute(delete(EmailCode).where(EmailCode.id == row_id))
        raise HTTPException(502, "Не удалось отправить письмо. Проверьте адрес или попробуйте позже") from e
    log.info("registration code sent to %s", email)
    return EMAIL_RESEND_S


async def register_by_email(
    s: AsyncSession, email: str, code: str, username: str, password: str, device_id: str
) -> auth.AuthResult:
    """Creates a local Hub account for an address confirmed by the code from its letter and signs in."""
    _check_email_signup()
    _check_rate()
    email, username = normalize_email(email), username.strip()
    check_new_account(username, password)
    row = (
        await s.execute(
            select(EmailCode)
            .where(EmailCode.email == email, EmailCode.used_at.is_(None))
            .order_by(EmailCode.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or row.expires_at < utcnow():
        raise _fail(400, "Код устарел или уже использован: запросите новое письмо")
    if not hmac.compare_digest(row.code_hash, _code_hash(email, re.sub(r"\D", "", code))):
        left = EMAIL_ATTEMPTS - row.attempts - 1
        async with session_scope() as w:  # counted although this request's own transaction rolls back
            await w.execute(
                update(EmailCode)
                .where(EmailCode.id == row.id)
                .values(attempts=EmailCode.attempts + 1, used_at=utcnow() if left <= 0 else None)
            )
        raise _fail(
            400, f"Неверный код: осталось попыток — {left}" if left > 0 else "Неверный код. Запросите новое письмо"
        )
    await _check_email_free(s, email)
    await _check_name_free(s, username)
    # Taken first, as an invite is: a second registration with the same letter finds it used.
    res = await s.execute(
        update(EmailCode)
        .where(EmailCode.id == row.id, EmailCode.used_at.is_(None))
        .values(used_at=utcnow())
        .returning(EmailCode.id)
    )
    if res.first() is None:
        raise _fail(400, "Код уже использован: запросите новое письмо")
    result = await _create_account(s, username, password, device_id, email=email)
    log.info("registered %s by email", username, extra={"user_id": result.user.id})
    return result


async def purge_email_codes() -> int:
    """Codes past the daily count are of no use; returns how many went."""
    async with session_scope() as s:
        res = await s.execute(
            delete(EmailCode).where(EmailCode.created_at < utcnow() - timedelta(days=2)).returning(EmailCode.id)
        )
        return len(res.all())


async def username_for_login(s: AsyncSession, login: str) -> str:
    """Sign-in takes the name, or the email the account registered with (kept for callers of the old helper)."""
    user = await auth.find_user_for_login(s, login)
    return user.username if user is not None else login


# --- external OIDC identities ---------------------------------------------------------------


def username_from_email(email: str | None) -> str:
    local = (email or "").split("@", 1)[0].lower()
    name = re.sub(r"[^\w.\-]", "", local)[:32]
    return name if len(name) >= 2 else f"user{secrets.randbelow(10000):04d}"


def username_from_claims(claims: dict[str, Any]) -> str:
    """A name for a new account from the provider's claims: preferred name, local part of the email, subject."""
    for key in ("preferred_username", "nickname", "email", "name"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            name = re.sub(r"[^\w.\-]", "", username_from_email(value) if "@" in value else value)
            if USERNAME_RE.match(name):
                return name
    return f"user{secrets.randbelow(10000):04d}"


async def free_username(s: AsyncSession, username: str) -> str:
    """`username` itself, or `username2`, `username3`… when the wanted name is taken."""
    base = username[:30]
    candidate, n = base, 1
    while (await s.execute(select(User.id).where(func.lower(User.username) == candidate.lower()))).first() is not None:
        n += 1
        candidate = f"{base}{n}"
    return candidate


async def linked_user(s: AsyncSession, issuer: str, subject: str) -> User | None:
    link = (
        await s.execute(select(OAuthLink).where(OAuthLink.issuer == issuer, OAuthLink.subject == subject))
    ).scalar_one_or_none()
    return await s.get(User, link.user_id) if link is not None else None


async def _check_oidc_free(s: AsyncSession, issuer: str, subject: str, user: User | None = None) -> None:
    other = await linked_user(s, issuer, subject)
    if other is not None and (user is None or other.id != user.id):
        raise HTTPException(409, f"Этот внешний аккаунт уже привязан к пользователю «{other.username}»")


async def oidc_login(
    s: AsyncSession,
    provider: str,
    issuer: str,
    claims: dict[str, Any],
    device_id: str,
    *,
    invite: str | None = None,
    username: str | None = None,
) -> auth.AuthResult:
    """Sign-in through an external provider: the linked account, or a new one with an active invite.

    An unlinked account without an invite is `409`: the Hub MUST NOT link accounts by email (account takeover).
    """
    user = await linked_user(s, issuer, str(claims["sub"]))
    if user is not None:
        return await auth.sign_in_user(s, user, device_id)
    if not invite:
        raise HTTPException(
            409,
            "Этот внешний аккаунт ещё не привязан. Введите код приглашения, "
            "а если аккаунт уже есть — войдите паролем и привяжите его в настройках",
        )
    _check_rate()
    inv = await _consume_invite(s, invite)
    wanted = (username or "").strip() or username_from_claims(claims)
    if username and not USERNAME_RE.match(username.strip()):
        raise HTTPException(400, "Имя: от 2 до 32 букв или цифр, можно «.», «_», «-», без пробелов")
    name = await free_username(s, wanted)
    result = await _create_account(
        s,
        name,
        None,  # no password of its own: only this OIDC identity, until the owner sets one
        device_id,
        claims=claims,
        provider=provider,
        email=claims.get("email") if claims.get("email_verified") in (True, "true") else None,
    )
    inv.used_by = result.user.id
    log.info("registered %s by OIDC %s and invite %s", name, provider, inv.id, extra={"user_id": result.user.id})
    return result


async def link_oidc(s: AsyncSession, user: User, provider: str, issuer: str, claims: dict[str, Any]) -> User:
    """Links `(issuer, subject)` to the signed-in user; a conflict with another account is `409`."""
    await _check_oidc_free(s, issuer, str(claims["sub"]), user)
    link = (
        await s.execute(select(OAuthLink).where(OAuthLink.user_id == user.id, OAuthLink.provider == provider))
    ).scalar_one_or_none()
    if link is None:
        s.add(OAuthLink(user_id=user.id, provider=provider, issuer=issuer, subject=str(claims["sub"])))
    else:
        link.issuer, link.subject = issuer, str(claims["sub"])
    if provider == "google":
        user.google_sub, user.google_email = str(claims["sub"]), claims.get("email")
    await s.flush()
    return user


async def unlink_oidc(s: AsyncSession, user: User, provider: str) -> User:
    """Removes a link when the account keeps another way in (a password or another link)."""
    links = list((await s.execute(select(OAuthLink).where(OAuthLink.user_id == user.id))).scalars())
    mine = [link for link in links if link.provider == provider]
    if not mine:
        raise HTTPException(404, "Этот внешний аккаунт не привязан")
    if not user.password_hash and len(links) == 1:
        raise HTTPException(409, "Это единственный способ входа: сначала задайте пароль")
    for link in mine:
        await s.delete(link)
    if provider == "google":
        user.google_sub = user.google_email = None
    await s.flush()
    return user


def linked_providers(links: list[OAuthLink]) -> list[str]:
    return sorted({link.provider for link in links})


# --- the legacy Google ID-token endpoint (Android app) --------------------------------------


async def _google_claims(id_token: str) -> dict[str, Any]:
    if not get_settings().google_client_id:
        raise HTTPException(404, "Вход через Google на этом сервере не настроен")
    try:
        return await verify_id_token(id_token)
    except GoogleTokenError as e:
        log.warning("google token rejected: %s", e)
        raise HTTPException(401, "Google не подтвердил вход, попробуйте ещё раз") from e


async def google_login(
    s: AsyncSession, id_token: str, code: str | None, username: str | None, device_id: str
) -> auth.AuthResult:
    """Sign-in with a Google ID token (the app gets it from Credential Manager): linked account or invite."""
    claims = await _google_claims(id_token)
    return await oidc_login(s, "google", GOOGLE_ISSUER, claims, device_id, invite=code, username=username)


async def link_google(s: AsyncSession, user: User, id_token: str) -> User:
    claims = await _google_claims(id_token)
    return await link_oidc(s, user, "google", GOOGLE_ISSUER, claims)
