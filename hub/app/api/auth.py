"""Sign-in, tokens, registration, password change and OIDC sign-in/link endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import accounts, auth, bootstrap, mailer, oidc, tokens
from app.api.schemas import (
    AuthOptionsOut,
    AuthSessionOut,
    AuthSessionsOut,
    BootstrapIn,
    EmailCodeIn,
    EmailCodeOut,
    EmailRegisterIn,
    GoogleIn,
    GoogleLinkIn,
    LoginIn,
    MeOut,
    OidcLinkIn,
    OidcProviderOut,
    PasswordIn,
    RegisterIn,
)
from app.config import get_settings
from app.db import get_session
from app.models import OAuthLink, User
from app.passwords import check_password_policy, hash_password, verify_password
from app.security import decrypt, encrypt

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _links(s: AsyncSession, user: User) -> list[str]:
    return accounts.linked_providers(
        list((await s.execute(select(OAuthLink).where(OAuthLink.user_id == user.id))).scalars())
    )


async def me_out(s: AsyncSession, user: User) -> MeOut:
    settings = get_settings()
    links = await _links(s, user)
    return MeOut(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        public_hub_url=settings.public_hub_url,
        google_email=user.google_email,
        email=user.email,
        must_change_password=user.must_change_password,
        oidc_links=links,
    )


def _device(request: Request) -> str:
    return request.cookies.get(tokens.DEVICE_COOKIE) or tokens.new_device_id()


def _signed_in(request: Request, response: Response, result: auth.AuthResult) -> None:
    auth.set_auth_cookies(request, response, result.access, result.refresh, result.device_id, result.family_id)


@router.get("/options", response_model=AuthOptionsOut)
async def options():
    settings = get_settings()
    return AuthOptionsOut(
        invites=True,
        email_signup=mailer.configured(),
        google_client_id=settings.google_client_id or None,
        oidc_providers=[OidcProviderOut(name=p.name, title=p.title) for p in oidc.enabled()],
    )


@router.post("/login", response_model=MeOut)
async def login(
    body: LoginIn, request: Request, response: Response, s: AsyncSession = Depends(get_session, scope="function")
):
    result = await auth.password_login(s, body.username, body.password, _device(request))
    _signed_in(request, response, result)
    return await me_out(s, result.user)


@router.post("/refresh", response_model=MeOut)
async def refresh(request: Request, response: Response, s: AsyncSession = Depends(get_session, scope="function")):
    """Rotates the refresh token of this device; a replay outside the window revokes the session."""
    result = await auth.refresh_session(
        s, request.cookies.get(tokens.REFRESH_COOKIE), request.cookies.get(tokens.DEVICE_COOKIE) or ""
    )
    _signed_in(request, response, result)
    return await me_out(s, result.user)


@router.post("/bootstrap", response_model=MeOut)
async def bootstrap_admin(
    body: BootstrapIn, request: Request, response: Response, s: AsyncSession = Depends(get_session, scope="function")
):
    result = await bootstrap.bootstrap_with_token(s, body.token, body.username, body.password, _device(request))
    _signed_in(request, response, result)
    return await me_out(s, result.user)


@router.post("/register", response_model=MeOut)
async def register(
    body: RegisterIn, request: Request, response: Response, s: AsyncSession = Depends(get_session, scope="function")
):
    result = await accounts.register(s, body.code, body.username, body.password, _device(request))
    _signed_in(request, response, result)
    return await me_out(s, result.user)


@router.post("/email/code", response_model=EmailCodeOut)
async def email_code(body: EmailCodeIn):
    return EmailCodeOut(resend_in=await accounts.send_email_code(body.email))


@router.post("/email/register", response_model=MeOut)
async def email_register(
    body: EmailRegisterIn,
    request: Request,
    response: Response,
    s: AsyncSession = Depends(get_session, scope="function"),
):
    result = await accounts.register_by_email(s, body.email, body.code, body.username, body.password, _device(request))
    _signed_in(request, response, result)
    return await me_out(s, result.user)


@router.post("/password", response_model=MeOut)
async def change_password(
    body: PasswordIn,
    request: Request,
    response: Response,
    user: User = Depends(auth.current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    """A new password in place of the current one (or the first one, for an OIDC-only account)."""
    if user.password_hash:
        if not body.current_password or not verify_password(user.password_hash, body.current_password):
            raise HTTPException(401, "Текущий пароль неверен")
    check_password_policy(user.username, body.new_password)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    family = await tokens.current_family(s, request.cookies.get(tokens.REFRESH_COOKIE))
    await tokens.revoke_other_sessions(s, user.id, family)
    await s.flush()
    return await me_out(s, user)


@router.get("/sessions", response_model=AuthSessionsOut)
async def sessions(
    request: Request,
    user: User = Depends(auth.current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    """The devices of the account: their last use and which one is the current device. Never the tokens."""
    current = await tokens.current_family(s, request.cookies.get(tokens.REFRESH_COOKIE))
    rows = await tokens.sessions_of(s, user.id)
    return AuthSessionsOut(
        sessions=[
            AuthSessionOut(
                id=row.family_id,
                device=_device_label(row),
                created_at=row.created_at,
                last_used_at=row.last_used_at,
                expires_at=row.expires_at,
                current=row.family_id == current,
            )
            for row in rows
        ]
    )


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: str, user: User = Depends(auth.current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    """Revokes a device: every refresh token of its session stops working."""
    if not await tokens.revoke_device(s, user.id, session_id):
        raise HTTPException(404, "Нет такой сессии")
    return {"ok": True}


def _device_label(row: tokens.SessionInfo) -> str:
    """A short human-readable device name: the `hm_device` cookie is random, so the id is all the Hub knows."""
    return f"Устройство {row.device_id[:6]}"


# --- external OIDC identities ---------------------------------------------------------------


def _verifier_only(request: Request, provider: str, state: str) -> oidc.Pending:
    pending = _pending(request, provider)
    if not secrets_compare(state, pending.state):
        raise HTTPException(400, "Ответ провайдера не совпал с запросом (state)")
    return pending


def secrets_compare(a: str, b: str) -> bool:
    import hmac

    # bytes: hmac.compare_digest refuses str with non-ASCII characters (TypeError -> 500)
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def _optional_user(request: Request, s: AsyncSession = Depends(get_session, scope="function")) -> User | None:
    """The signed-in user, or None for an anonymous request (`401`); a `403` keeps its meaning."""
    try:
        return await auth.current_user(request, s)
    except HTTPException as e:
        if e.status_code == 403:
            raise
        return None


async def _required_user(request: Request, s: AsyncSession) -> User:
    user = await _optional_user(request, s)
    if user is None:
        raise HTTPException(401, "Требуется вход")
    return user


@router.get("/oidc/{provider}/start")
async def oidc_start(
    provider: str,
    request: Request,
    mode: str = Query("login", pattern="^(login|link)$"),
    invite: str | None = None,
    user: User | None = Depends(_optional_user),
):
    """Starts an Authorization Code + PKCE flow: redirects to the provider with state/nonce in a pending cookie."""
    if mode == "link" and user is None:
        raise HTTPException(401, "Требуется вход")
    p = oidc.provider(provider)
    pending, _ = oidc.new_pending(p, mode, invite)
    settings = get_settings()
    url = await oidc.authorization_url(
        p, oidc.redirect_uri(settings.public_hub_url, str(request.base_url), p.name), pending
    )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        oidc.PENDING_COOKIE,
        encrypt(pending.to_json()),
        max_age=oidc.PENDING_TTL_S,
        httponly=True,
        samesite="lax",
        secure=auth._cookies_secure(request),
        path="/api/auth",
    )
    return response


@router.get("/oidc/{provider}/callback")
async def oidc_callback(
    provider: str,
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
    error: str | None = None,
    s: AsyncSession = Depends(get_session, scope="function"),
):
    """The provider's redirect target: completes the flow, sets the cookies and goes back to the PWA."""
    p = oidc.provider(provider)
    pending = _pending(request, p.name)
    if error:
        raise HTTPException(400, "Провайдер отклонил вход: попробуйте снова")
    if not code or not state:
        raise HTTPException(400, "Провайдер не вернул код авторизации")
    if not secrets_compare(state, pending.state):
        raise HTTPException(400, "Ответ провайдера не совпал с запросом (state)")
    settings = get_settings()
    claims = await oidc.exchange_code(
        p, code, pending, oidc.redirect_uri(settings.public_hub_url, str(request.base_url), p.name)
    )
    result = await _finish_oidc(s, request, provider, p, pending, claims)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(oidc.PENDING_COOKIE, path="/api/auth")
    _signed_in(request, response, result)
    return response


@router.post("/oidc/link/{provider}", response_model=MeOut)
async def oidc_link(
    provider: str,
    body: OidcLinkIn,
    request: Request,
    user: User = Depends(auth.current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    """Links the identity behind a code (a client that handled the redirect itself) to the signed-in account."""
    p = oidc.provider(provider)
    claims = await oidc.exchange_code(
        p,
        body.code,
        _verifier_only(request, p.name, body.state),
        oidc.redirect_uri(get_settings().public_hub_url, str(request.base_url), p.name),
    )
    await accounts.link_oidc(s, user, p.name, p.issuer, claims)
    return await me_out(s, user)


@router.delete("/oidc/link/{provider}", response_model=MeOut)
async def oidc_unlink(
    provider: str, user: User = Depends(auth.current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    return await me_out(s, await accounts.unlink_oidc(s, user, provider))


async def _finish_oidc(
    s: AsyncSession, request: Request, provider: str, p: oidc.Provider, pending: oidc.Pending, claims: dict
) -> auth.AuthResult:
    if pending.mode == "link":
        user = await _required_user(request, s)
        await accounts.link_oidc(s, user, p.name, p.issuer, claims)
        return await auth.sign_in_user(s, user, _device(request))
    return await accounts.oidc_login(s, provider, p.issuer, claims, _device(request), invite=pending.invite)


def _pending(request: Request, provider: str) -> oidc.Pending:
    raw = request.cookies.get(oidc.PENDING_COOKIE)
    if not raw:
        raise HTTPException(400, "Начатый вход устарел: начните его заново")
    try:
        pending = oidc.Pending.from_json(decrypt(raw))
    except RuntimeError as e:
        raise HTTPException(400, "Начатый вход устарел: начните его заново") from e
    if pending.provider != provider:
        raise HTTPException(400, "Ответ провайдера не совпал с запросом (provider)")
    return pending


# --- the legacy Google ID-token flow (the Android app's Credential Manager) ------------------


@router.post("/google", response_model=MeOut)
async def google(
    body: GoogleIn, request: Request, response: Response, s: AsyncSession = Depends(get_session, scope="function")
):
    result = await accounts.google_login(s, body.id_token, body.code, body.username, _device(request))
    _signed_in(request, response, result)
    return await me_out(s, result.user)


@router.post("/google/link", response_model=MeOut)
async def google_link(
    body: GoogleLinkIn,
    user: User = Depends(auth.current_user),
    s: AsyncSession = Depends(get_session, scope="function"),
):
    return await me_out(s, await accounts.link_google(s, user, body.id_token))


@router.delete("/google/link", response_model=MeOut)
async def google_unlink(
    user: User = Depends(auth.current_user), s: AsyncSession = Depends(get_session, scope="function")
):
    return await me_out(s, await accounts.unlink_oidc(s, user, "google"))


@router.post("/logout")
async def logout(request: Request, response: Response, s: AsyncSession = Depends(get_session, scope="function")):
    await auth.logout(s, request, response)
    return {"ok": True}


@router.get("/me", response_model=MeOut)
async def me(user: User = Depends(auth.current_user), s: AsyncSession = Depends(get_session, scope="function")):
    return await me_out(s, user)
