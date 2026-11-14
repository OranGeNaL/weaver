"""External OIDC providers as Relying Parties: discovery, Authorization Code + PKCE, id_token verification.

The Hub is never an OIDC Provider itself: it only signs users in and links `(iss, sub)` to a local account. Google is
a built-in provider (discovery at accounts.google.com); any other provider comes from `config.yaml` (`oidc.providers`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config import OidcProviderCfg, get_settings
from app.security import decrypt

log = logging.getLogger(__name__)

GOOGLE_NAME = "google"
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"
PENDING_COOKIE = "hm_oidc"
PENDING_TTL_S = 600
SKEW_S = 120


class OidcError(Exception):
    """A failed OIDC step with the HTTP status and Russian message the endpoint returns."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Provider:
    name: str
    title: str
    issuer: str
    client_id: str
    client_secret: str
    scopes: tuple[str, ...]
    discovery_url: str


@dataclass(frozen=True)
class Pending:
    """The OIDC exchange state kept in the (encrypted, HttpOnly) cookie between the redirect and the callback."""

    provider: str
    state: str
    nonce: str
    verifier: str
    mode: str  # login / link
    invite: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "provider": self.provider,
                "state": self.state,
                "nonce": self.nonce,
                "verifier": self.verifier,
                "mode": self.mode,
                "invite": self.invite,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> Pending:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise OidcError(400, "Начатый вход устарел: начните его заново") from e
        return cls(
            provider=data["provider"],
            state=data["state"],
            nonce=data["nonce"],
            verifier=data["verifier"],
            mode=data.get("mode", "login"),
            invite=data.get("invite"),
        )


def _secret_of(cfg: OidcProviderCfg) -> str:
    """The client secret: Fernet-encrypted `client_secret_enc` wins over the plain field."""
    if cfg.client_secret_enc:
        try:
            return decrypt(cfg.client_secret_enc)
        except RuntimeError as e:
            raise OidcError(500, "Секрет OIDC-провайдера не удалось расшифровать: проверьте HUB_SECRET_KEY") from e
    return cfg.client_secret


def _providers() -> dict[str, Provider]:
    settings = get_settings()
    out: dict[str, Provider] = {}
    if settings.google_client_id:
        out[GOOGLE_NAME] = Provider(
            name=GOOGLE_NAME,
            title="Google",
            issuer=GOOGLE_ISSUER,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            scopes=("openid", "email", "profile"),
            discovery_url=GOOGLE_DISCOVERY,
        )
    for name, cfg in settings.cfg.oidc.providers.items():
        if not cfg.enabled or not cfg.issuer or not cfg.client_id:
            continue
        try:
            secret = _secret_of(cfg)
        except OidcError as e:
            # one unreadable secret (HUB_SECRET_KEY changed) must not take the whole sign-in screen down
            log.warning("OIDC provider %s is skipped: %s", name, e.detail)
            continue
        out[name] = Provider(
            name=name,
            title=cfg.title or name.title(),
            issuer=cfg.issuer.rstrip("/"),
            client_id=cfg.client_id,
            client_secret=secret,
            scopes=tuple(cfg.scopes),
            discovery_url=cfg.discovery_url or f"{cfg.issuer.rstrip('/')}/.well-known/openid-configuration",
        )
    return out


def enabled() -> list[Provider]:
    return list(_providers().values())


def provider(name: str) -> Provider:
    """A configured provider, or 404: the sign-in screen only offers what the server has."""
    found = _providers().get(name)
    if found is None:
        raise OidcError(404, "Такой вход на этом сервере не настроен")
    return found


_docs: dict[str, tuple[dict[str, Any], float]] = {}
_keys: dict[str, tuple[dict[str, rsa.RSAPublicKey], float]] = {}


def _client() -> httpx.AsyncClient:
    settings = get_settings()
    proxy = settings.outbound_proxy or None
    return httpx.AsyncClient(timeout=15, proxy=proxy)


async def discovery(p: Provider) -> dict[str, Any]:
    """The provider's discovery document (cached): 502 when it does not answer."""
    cached = _docs.get(p.issuer)
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        async with _client() as client:
            r = await client.get(p.discovery_url)
            r.raise_for_status()
            doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise OidcError(502, "OIDC-провайдер не отвечает: попробуйте позже") from e
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(key):
            raise OidcError(502, "OIDC-провайдер вернул неполный документ discovery")
    _docs[p.issuer] = (doc, time.time() + 3600)
    return doc


def new_pending(p: Provider, mode: str, invite: str | None = None) -> tuple[Pending, str]:
    """A fresh state/nonce/PKCE verifier and the S256 challenge for the authorization request."""
    verifier = secrets.token_urlsafe(48)
    pending = Pending(
        provider=p.name,
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        verifier=verifier,
        mode=mode,
        invite=invite,
    )
    return pending, code_challenge(verifier)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def authorization_url(p: Provider, redirect_uri: str, pending: Pending) -> str:
    doc = await discovery(p)
    params = {
        "response_type": "code",
        "client_id": p.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(p.scopes),
        "state": pending.state,
        "nonce": pending.nonce,
        "code_challenge": code_challenge(pending.verifier),
        "code_challenge_method": "S256",
    }
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return f"{doc['authorization_endpoint']}{sep}{urlencode(params)}"


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _int(part: str) -> int:
    return int.from_bytes(_b64(part), "big")


async def _jwks(uri: str) -> dict[str, rsa.RSAPublicKey]:
    cached = _keys.get(uri)
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        async with _client() as client:
            r = await client.get(uri)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise OidcError(502, "OIDC-провайдер не отвечает: попробуйте позже") from e
    keys = {}
    for k in data.get("keys", []):
        if k.get("kty") == "RSA" and k.get("kid"):
            keys[str(k["kid"])] = rsa.RSAPublicNumbers(_int(k["e"]), _int(k["n"])).public_key()
    m = re.search(r"max-age=(\d+)", r.headers.get("cache-control", ""))
    _keys[uri] = (keys, time.time() + (int(m.group(1)) if m else 3600))
    return keys


async def verify_id_token(p: Provider, token: str, nonce: str) -> dict[str, Any]:
    """Claims of a valid id_token of this provider (signature, issuer, audience, expiry, nonce)."""
    doc = await discovery(p)
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64(head_b64))
        claims = json.loads(_b64(body_b64))
        signature = _b64(sig_b64)
    except (ValueError, json.JSONDecodeError) as e:
        raise OidcError(400, "Провайдер вернул некорректный id_token") from e
    if header.get("alg") != "RS256":
        raise OidcError(400, "Провайдер подписал токен неподдерживаемым алгоритмом")
    keys = await _jwks(doc["jwks_uri"])
    key = keys.get(str(header.get("kid")))
    if key is None:
        keys = await _jwks(doc["jwks_uri"])
        key = keys.get(str(header.get("kid")))
    if key is None:
        raise OidcError(400, "Провайдер подписал токен неизвестным ключом")
    try:
        key.verify(signature, f"{head_b64}.{body_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise OidcError(400, "Подпись id_token не сошлась") from e
    now = time.time()
    issuer = str(claims.get("iss") or "").rstrip("/")
    if issuer not in {p.issuer.rstrip("/"), str(doc.get("issuer") or "").rstrip("/")}:
        raise OidcError(400, "id_token выпущен другим провайдером")
    aud = claims.get("aud")
    if aud != p.client_id and not (isinstance(aud, list) and p.client_id in aud):
        raise OidcError(400, "id_token выпущен для другого приложения")
    if float(claims.get("exp", 0)) < now - SKEW_S or float(claims.get("iat", now)) > now + SKEW_S:
        raise OidcError(400, "id_token истёк: попробуйте войти снова")
    if not nonce or not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise OidcError(400, "Ответ провайдера не совпал с запросом (nonce)")
    if not claims.get("sub"):
        raise OidcError(400, "Провайдер не вернул идентификатор пользователя")
    return claims


async def exchange_code(p: Provider, code: str, pending: Pending, redirect_uri: str) -> dict[str, Any]:
    """Turns the authorization code into verified claims (token endpoint + id_token)."""
    doc = await discovery(p)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": p.client_id,
        "code_verifier": pending.verifier,
    }
    if p.client_secret:
        data["client_secret"] = p.client_secret
    try:
        async with _client() as client:
            r = await client.post(doc["token_endpoint"], data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise OidcError(502, "OIDC-провайдер не отвечает: попробуйте позже") from e
    if r.status_code >= 400:
        raise OidcError(400, "Провайдер не подтвердил вход: попробуйте снова")
    try:
        tokens = r.json()
    except ValueError as e:
        raise OidcError(502, "Провайдер вернул некорректный ответ") from e
    id_token = tokens.get("id_token")
    if not id_token:
        # no id_token in the token response: the userinfo endpoint is the only source of the subject
        raise OidcError(502, "Провайдер не вернул id_token")
    return await verify_id_token(p, str(id_token), pending.nonce)


def redirect_uri(public_hub_url: str, request_base: str, name: str) -> str:
    base = (public_hub_url or request_base).rstrip("/")
    return f"{base}/api/auth/oidc/{name}/callback"
