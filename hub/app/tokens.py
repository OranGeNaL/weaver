"""Hub-issued tokens: a short-lived access JWT and a rotating opaque refresh token.

Access — HS256 JWT signed with `JWT_SIGNING_KEY` (15 minutes): `sub`, `iat`, `exp`, `jti`, `role`. Refresh — a random
opaque token kept in `refresh_tokens` as a SHA-256 with its `family_id` (the session) and `device_id` (the HttpOnly
`hm_device` cookie). Every refresh rotates the token; the pair it was exchanged for is kept encrypted in
`replacement_enc` for the idempotency window, so a parallel refresh of the same device gets the same pair.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, true, update
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import RefreshToken, User, utcnow
from app.security import decrypt, encrypt, hash_token, new_token

ACCESS_COOKIE = "hm_session"
REFRESH_COOKIE = "hm_refresh"
DEVICE_COOKIE = "hm_device"
REFRESH_PATH = "/api/auth"

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)
# how long a rotated token may be replayed with the same device and get the stored pair back
IDEMPOTENCY_WINDOW = timedelta(seconds=10)

JWT_ALG = "HS256"
JWT_KID = "hm1"


class TokenError(Exception):
    """The token is malformed, unsigned by us or expired."""


@dataclass(frozen=True)
class Pair:
    """A freshly issued pair of tokens and the session (family) it belongs to."""

    access: str
    refresh: str
    family_id: str


class UnreadableReplacement(Exception):
    """The stored rotation pair cannot be decrypted: the key changed. Not a reuse attack."""


class RefreshError(Exception):
    """Refresh failed: `status` 401 with a Russian message for the client."""

    def __init__(self, detail: str = "Сессия истекла, войдите снова"):
        super().__init__(detail)
        self.detail = detail


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signing_key() -> bytes:
    key = get_settings().jwt_signing_key
    if not key:
        raise RuntimeError("JWT_SIGNING_KEY is not set")
    return key.encode("utf-8")


def _sign(signing_input: bytes) -> str:
    return _b64(hmac.new(_signing_key(), signing_input, hashlib.sha256).digest())


def issue_access(user: User, now: datetime | None = None) -> str:
    """A signed access JWT for the user: identification only, the role is re-checked against the database."""
    now = now or utcnow()
    header = {"alg": JWT_ALG, "typ": "JWT", "kid": JWT_KID}
    claims = {
        "sub": str(user.id),
        "iat": int(now.timestamp()),
        "exp": int((now + ACCESS_TTL).timestamp()),
        "jti": secrets.token_urlsafe(16),
        "role": "admin" if user.is_admin else "user",
    }
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}."
    signing_input += _b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{signing_input}.{_sign(signing_input.encode('ascii'))}"


def decode_access(token: str, now: datetime | None = None) -> dict:
    """Claims of a valid access JWT; TokenError when the signature or the expiry does not hold."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_unb64(head_b64))
        claims = json.loads(_unb64(body_b64))
    except (ValueError, json.JSONDecodeError) as e:
        raise TokenError("malformed token") from e
    if header.get("alg") != JWT_ALG or header.get("kid") != JWT_KID:
        raise TokenError("unexpected token type")
    if not hmac.compare_digest(_sign(f"{head_b64}.{body_b64}".encode("ascii")), sig_b64):
        raise TokenError("bad signature")
    now = now or utcnow()
    if float(claims.get("exp", 0)) <= now.timestamp():
        raise TokenError("token expired")
    if not str(claims.get("sub") or "").isdigit():
        raise TokenError("no subject")
    return claims


def new_device_id() -> str:
    return secrets.token_urlsafe(24)


def _new_token_row(user: User, device_id: str, family_id: str, now: datetime) -> tuple[str, RefreshToken]:
    token, token_hash = new_token()
    row = RefreshToken(
        id=token_hash,
        user_id=user.id,
        family_id=family_id,
        device_id=device_id,
        created_at=now,
        expires_at=now + REFRESH_TTL,
    )
    return token, row


async def issue_pair(s: AsyncSession, user: User, device_id: str, family_id: str | None = None) -> Pair:
    """A new access and refresh token; a new session (family) unless one is given."""
    now = utcnow()
    family = family_id or secrets.token_urlsafe(18)
    refresh, row = _new_token_row(user, device_id, family, now)
    s.add(row)
    await s.flush()
    return Pair(access=issue_access(user, now), refresh=refresh, family_id=family)


async def _lock_rows(s: AsyncSession, *conditions) -> None:
    """Locks the matching refresh rows in ascending `id` order.

    Every multi-row operation of this module goes through it, so two transactions that touch overlapping rows
    (a stale replay vs a live refresh, logout vs refresh, a password change vs refresh) acquire them in the same
    order and cannot deadlock each other.
    """
    await s.execute(select(RefreshToken.id).where(*conditions).order_by(RefreshToken.id).with_for_update())


async def revoke_family(s: AsyncSession, family_id: str, keep_device: str | None = None) -> None:
    """Revokes every refresh token of the session (and drops what rotation kept for replays)."""
    await _lock_rows(s, RefreshToken.family_id == family_id)
    await s.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
            true() if keep_device is None else RefreshToken.device_id != keep_device,
        )
        .values(revoked_at=utcnow(), replacement_enc=None)
    )


async def revoke_other_sessions(s: AsyncSession, user_id: int, keep_family: str | None) -> None:
    """Revokes every session of the user except `keep_family` (a password change, a CLI reset)."""
    await _lock_rows(s, RefreshToken.user_id == user_id)
    await s.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            true() if keep_family is None else RefreshToken.family_id != keep_family,
        )
        .values(revoked_at=utcnow(), replacement_enc=None)
    )


async def revoke_device(s: AsyncSession, user_id: int, family_id: str) -> bool:
    """Revokes one device's session; False when the session is not the user's."""
    row = (
        await s.execute(
            select(RefreshToken.id).where(RefreshToken.family_id == family_id, RefreshToken.user_id == user_id)
        )
    ).first()
    if row is None:
        return False
    await revoke_family(s, family_id)
    return True


async def rotate(s: AsyncSession, refresh_token: str, device_id: str) -> tuple[Pair, User]:
    """Rotates a refresh token. A replay outside the idempotency window revokes the whole session.

    The family's rows are locked in ascending id order before anything is read or changed, so a simultaneous
    refresh of the same device, a stale replay and a logout/revoke serialize instead of deadlocking.
    """
    now = utcnow()
    row = await s.get(RefreshToken, hash_token(refresh_token))
    if row is None:
        raise RefreshError()
    # serialize every operation of this session on its rows (ascending id), then read the state under the lock
    await _lock_rows(s, RefreshToken.family_id == row.family_id)
    try:
        await s.refresh(row)  # another request may have rotated the token while this one waited
    except InvalidRequestError as e:  # pragma: no cover - the daily purge keeps rows for two refresh lifetimes
        raise RefreshError() from e
    if row.revoked_at is not None:
        # already rotated: inside the window (same device) return the stored pair, outside it revoke the session
        try:
            pair = _replay(row, device_id, now)
        except UnreadableReplacement as e:
            # HUB_SECRET_KEY changed: no session, but the family is not punished for the operator's rotation
            raise RefreshError() from e
        if pair is not None:
            user = await s.get(User, row.user_id)
            if user is None:
                raise RefreshError()
            return pair, user
        await revoke_family(s, row.family_id)
        raise RefreshError("Сессия была отозвана: войдите снова")
    if row.expires_at <= now or row.device_id != device_id:
        await revoke_family(s, row.family_id)
        raise RefreshError()
    user = await s.get(User, row.user_id)
    if user is None:
        await revoke_family(s, row.family_id)
        raise RefreshError()
    pair = await issue_pair(s, user, device_id, row.family_id)
    row.revoked_at = now
    row.rotated_at = now
    row.replacement_enc = encrypt(json.dumps({"access": pair.access, "refresh": pair.refresh}))
    # the previous generations' replays are over: only the pair just issued stays decryptable
    await s.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == row.family_id, RefreshToken.id != row.id)
        .values(replacement_enc=None)
    )
    user.last_seen_at = now
    await s.flush()
    return pair, user


def _replay(row: RefreshToken, device_id: str, now: datetime) -> Pair | None:
    """The pair stored for a replay inside the window; None outside it or with another device."""
    if not row.replacement_enc or row.rotated_at is None or row.device_id != device_id:
        return None
    if now - row.rotated_at > IDEMPOTENCY_WINDOW:
        return None
    try:
        stored = json.loads(decrypt(row.replacement_enc))
        return Pair(access=stored["access"], refresh=stored["refresh"], family_id=row.family_id)
    except (RuntimeError, ValueError, KeyError) as e:
        raise UnreadableReplacement(str(e)) from e


async def current_family(s: AsyncSession, refresh_token: str | None) -> str | None:
    """The session (family) of a refresh cookie, for "keep the current device" operations."""
    if not refresh_token:
        return None
    return (
        await s.execute(select(RefreshToken.family_id).where(RefreshToken.id == hash_token(refresh_token)))
    ).scalar_one_or_none()


@dataclass(frozen=True)
class SessionInfo:
    """One live session (family) of a device: what the «Мои устройства» screen shows."""

    family_id: str
    device_id: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime


async def sessions_of(s: AsyncSession, user_id: int) -> list[SessionInfo]:
    """One row per live session: revoked families are gone, the newest rotation is the last use."""
    rows = list(
        (
            await s.execute(
                select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.expires_at > utcnow())
            )
        ).scalars()
    )
    families: dict[str, list[RefreshToken]] = {}
    for row in rows:
        families.setdefault(row.family_id, []).append(row)
    out: list[SessionInfo] = []
    for family_id, family in families.items():
        live = [row for row in family if row.revoked_at is None]
        if not live:
            continue  # logged out, revoked or replaced: not a device of the account any more
        current = max(live, key=lambda row: row.created_at)
        out.append(
            SessionInfo(
                family_id=family_id,
                device_id=current.device_id,
                created_at=min(row.created_at for row in family),
                last_used_at=max(row.created_at for row in family),
                expires_at=current.expires_at,
            )
        )
    out.sort(key=lambda session: session.last_used_at, reverse=True)
    return out


async def purge_expired(s: AsyncSession) -> int:
    """Deletes refresh rows that expired long ago; runs from the daily housekeeping, not from a request."""
    res = await s.execute(
        delete(RefreshToken).where(RefreshToken.expires_at < utcnow() - REFRESH_TTL).returning(RefreshToken.id)
    )
    return len(res.all())
