"""Local accounts: password sign-in, tokens, bootstrap, password change, invites, email, OIDC and the admin list."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from tests.support import app_client

CLIENT_ID = "123-web.apps.googleusercontent.com"
CERTS = "https://www.googleapis.com/oauth2/v3/certs"
IDP = "https://idp.test"
OIDC_CLIENT = "hub-client"
OIDC_SECRET = "oidc-secret"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwks() -> dict:
    n = KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": "k1",
                "alg": "RS256",
                "use": "sig",
                "n": _b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")),
                "e": _b64(n.e.to_bytes(3, "big")),
            }
        ]
    }


def signed(claims: dict) -> str:
    head = _b64(json.dumps({"alg": "RS256", "kid": "k1", "typ": "JWT"}).encode())
    body = _b64(json.dumps(claims).encode())
    sig = KEY.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(sig)}"


def google_token(sub: str = "g-1", email: str = "anna.k@gmail.com", **over) -> str:
    now = int(time.time())
    return signed(
        {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": sub,
            "email": email,
            "email_verified": True,
            "iat": now,
            "exp": now + 3600,
            "name": "Анна",
            **over,
        }
    )


def idp_token(sub: str = "idp-1", email: str = "gleb@mail.test", nonce: str = "n", **over) -> str:
    now = int(time.time())
    return signed(
        {
            "iss": IDP,
            "aud": OIDC_CLIENT,
            "sub": sub,
            "email": email,
            "email_verified": True,
            "iat": now,
            "exp": now + 3600,
            "nonce": nonce,
            **over,
        }
    )


def discovery() -> dict:
    return {
        "issuer": IDP,
        "authorization_endpoint": f"{IDP}/authorize",
        "token_endpoint": f"{IDP}/token",
        "jwks_uri": f"{IDP}/jwks",
    }


def mock_idp(mock: respx.MockRouter) -> None:
    mock.get(f"{IDP}/.well-known/openid-configuration").mock(return_value=httpx.Response(200, json=discovery()))
    mock.get(f"{IDP}/jwks").mock(return_value=httpx.Response(200, json=jwks()))
    mock.get(CERTS).mock(return_value=httpx.Response(200, json=jwks(), headers={"cache-control": "max-age=600"}))


@pytest.fixture
async def env(hub_env, monkeypatch, tmp_path):
    """The app with the admin created from the environment and one generic OIDC provider (`idp.test`)."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
oidc:
  providers:
    fake:
      issuer: {IDP}
      client_id: {OIDC_CLIENT}
      client_secret: {OIDC_SECRET}
      title: Fake
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(config))

    from app import config as app_config
    from app.main import create_app

    app_config.get_settings.cache_clear()
    app = create_app()
    async with app.router.lifespan_context(app):
        with respx.mock(assert_all_called=False) as mock:
            mock_idp(mock)
            transport = httpx.ASGITransport(app=app)

            def client() -> httpx.AsyncClient:
                return httpx.AsyncClient(transport=transport, base_url="http://hub.test")

            async with app_client(app) as admin:
                yield {"admin": admin, "client": client, "mock": mock, "app": app}


@pytest.fixture
async def fresh_env(hub_env, monkeypatch):
    """An empty database without an administrator in the environment: the bootstrap endpoints are the only way."""
    from app import config
    from app.main import create_app

    monkeypatch.setenv("ADMIN_USERNAME", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "one-time-token")
    config.get_settings.cache_clear()
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)

        def client() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, base_url="http://hub.test")

        async with client() as admin:
            yield {"client": client, "app": app, "admin": admin}


async def make_invite(admin: httpx.AsyncClient, note: str = "Анна") -> dict:
    r = await admin.post("/api/admin/invites", json={"note": note})
    assert r.status_code == 200, r.text
    return r.json()


def pending_cookie(client: httpx.AsyncClient) -> dict:
    """The OIDC state the server keeps between the redirect and the callback (state/nonce/PKCE verifier)."""
    from app.security import decrypt

    raw = client.cookies.get("hm_oidc")
    assert raw, "no pending OIDC cookie"
    return json.loads(decrypt(raw))


# --- sign-in and the attempt limiter --------------------------------------------------------


async def test_login_is_local(env):
    async with env["client"]() as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "admin" and r.json()["is_admin"] is True
        me = (await c.get("/api/auth/me")).json()
        assert me["username"] == "admin" and me["must_change_password"] is False


async def test_wrong_password_and_unknown_user(env):
    async with env["client"]() as c:
        assert (await c.post("/api/auth/login", json={"username": "admin", "password": "wrong"})).status_code == 401
        assert (await c.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})).status_code == 401


async def test_login_attempts_are_limited_in_postgres(env):
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.models import LoginAttempt

    async with env["client"]() as c:
        for _ in range(3):
            assert (await c.post("/api/auth/login", json={"username": "admin", "password": "wrong"})).status_code == 401
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 429, r.text
        assert int(r.headers["retry-after"]) > 0
    async with session_scope() as s:  # the limiter lives in the database: a restart does not forget it
        assert (await s.execute(select(func.count(LoginAttempt.id)))).scalar_one() == 3
    async with env["client"]() as c:
        assert (await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})).status_code == 429


async def test_stale_attempts_do_not_limit_forever(env):
    from sqlalchemy import update

    from app.db import session_scope
    from app.models import LoginAttempt, utcnow

    async with env["client"]() as c:
        for _ in range(3):
            await c.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    async with session_scope() as s:
        await s.execute(update(LoginAttempt).values(attempted_at=utcnow() - timedelta(minutes=5)))
    async with env["client"]() as c:
        assert (await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})).status_code == 200


async def test_sessions_and_logout(env):
    async with env["client"]() as phone, env["client"]() as laptop:
        for c in (phone, laptop):
            assert (
                await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
            ).status_code == 200
        listed = (await phone.get("/api/auth/sessions")).json()["sessions"]
        # the fixture's admin client, the phone and the laptop
        assert len(listed) == 3
        assert sum(1 for s in listed if s["current"]) == 1
        # the laptop is revoked by its device id; its refresh stops working
        label = f"Устройство {laptop.cookies.get('hm_device', path='/api/auth')[:6]}"
        other = next(s for s in listed if s["device"] == label)
        assert (await phone.delete(f"/api/auth/sessions/{other['id']}")).json() == {"ok": True}
        assert (await laptop.post("/api/auth/refresh")).status_code == 401
        # logout revokes the current session
        assert (await phone.post("/api/auth/logout")).json() == {"ok": True}
        assert (await phone.post("/api/auth/refresh")).status_code == 401
        assert (await phone.get("/api/auth/me")).status_code == 401


async def test_refresh_rotates_and_a_parallel_replay_gets_the_same_pair(env):
    async with env["client"]() as c:
        await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        old = c.cookies.get("hm_refresh", path="/api/auth")
        first = await c.post("/api/auth/refresh")
        assert first.status_code == 200, first.text
        rotated = c.cookies.get("hm_refresh", path="/api/auth")
        assert rotated and rotated != old
        # the same device replays the old token inside the window: the stored pair, no second token
        c.cookies.set("hm_refresh", old, path="/api/auth", domain="hub.test")
        second = await c.post("/api/auth/refresh")
        assert second.status_code == 200, second.text
        assert c.cookies.get("hm_refresh", path="/api/auth") == rotated
        assert (await c.get("/api/auth/me")).status_code == 200


async def test_refresh_reuse_outside_the_window_revokes_the_session(env):
    from sqlalchemy import update

    from app.db import session_scope
    from app.models import RefreshToken, utcnow

    async with env["client"]() as c:
        await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        old = c.cookies.get("hm_refresh", path="/api/auth")
        assert (await c.post("/api/auth/refresh")).status_code == 200
        async with session_scope() as s:  # the idempotency window is 10 seconds
            await s.execute(update(RefreshToken).values(rotated_at=utcnow() - timedelta(seconds=30)))
        c.cookies.set("hm_refresh", old, path="/api/auth", domain="hub.test")
        r = await c.post("/api/auth/refresh")
        assert r.status_code == 401, r.text
        # the whole session is gone, not only the replayed token
        assert (await c.post("/api/auth/refresh")).status_code == 401


# --- bootstrap ----------------------------------------------------------------------------


async def test_bootstrap_from_environment_is_idempotent(env):
    from sqlalchemy import select

    from app.bootstrap import bootstrap_from_env
    from app.db import session_scope
    from app.models import User

    async with env["client"]() as c:
        assert (await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})).status_code == 200
    await bootstrap_from_env()
    async with session_scope() as s:
        users = list((await s.execute(select(User))).scalars())
    assert [u.username for u in users] == ["admin"]


async def test_no_default_admin_without_environment(fresh_env):
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.models import User

    async with fresh_env["client"]() as c:
        assert (await c.post("/api/auth/login", json={"username": "admin", "password": "admin"})).status_code == 401
        assert (await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})).status_code == 401
    async with session_scope() as s:
        assert (await s.execute(select(func.count(User.id)))).scalar_one() == 0


async def test_bootstrap_token_creates_the_admin_once(fresh_env):
    async with fresh_env["client"]() as c:
        r = await c.post("/api/auth/bootstrap", json={"token": "wrong", "username": "boss", "password": "boss-pass1"})
        assert r.status_code == 401
        r = await c.post(
            "/api/auth/bootstrap", json={"token": "one-time-token", "username": "boss", "password": "boss-pass1"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["is_admin"] is True and r.json()["must_change_password"] is True
        # the first password must be changed before anything else
        assert (await c.get("/api/admin/users")).status_code == 403
        assert (await c.get("/api/auth/me")).status_code == 200
    async with fresh_env["client"]() as c:
        r = await c.post(
            "/api/auth/bootstrap", json={"token": "one-time-token", "username": "boss2", "password": "boss-pass2"}
        )
        assert r.status_code == 409


async def test_bootstrap_rejects_a_weak_password(fresh_env):
    async with fresh_env["client"]() as c:
        r = await c.post("/api/auth/bootstrap", json={"token": "one-time-token", "username": "boss", "password": "123"})
        assert r.status_code == 400
        r = await c.post(
            "/api/auth/bootstrap", json={"token": "one-time-token", "username": "boss", "password": "password"}
        )
        assert r.status_code == 400


# --- password change ------------------------------------------------------------------------


async def test_password_change_with_must_change_password_gate(fresh_env):
    async with fresh_env["client"]() as c:
        await c.post(
            "/api/auth/bootstrap", json={"token": "one-time-token", "username": "boss", "password": "boss-pass1"}
        )
        assert (await c.post("/api/auth/refresh")).status_code == 200  # refresh is allowed
        r = await c.post("/api/auth/password", json={"current_password": "nope", "new_password": "new-pass1"})
        assert r.status_code == 401
        r = await c.post("/api/auth/password", json={"current_password": "boss-pass1", "new_password": "new-pass1"})
        assert r.status_code == 200, r.text
        assert r.json()["must_change_password"] is False
        assert (await c.get("/api/admin/users")).status_code == 200
    async with fresh_env["client"]() as c:
        assert (await c.post("/api/auth/login", json={"username": "boss", "password": "new-pass1"})).status_code == 200


async def test_password_change_revokes_the_other_devices(env):
    async with env["client"]() as phone, env["client"]() as laptop:
        for c in (phone, laptop):
            await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        r = await phone.post("/api/auth/password", json={"current_password": "adminpw", "new_password": "adminpw2"})
        assert r.status_code == 200, r.text
        # the current device still refreshes, the other one is out
        assert (await phone.post("/api/auth/refresh")).status_code == 200
        assert (await laptop.post("/api/auth/refresh")).status_code == 401


async def test_cli_reset_sets_must_change_password(env):
    from app.bootstrap import reset_password

    async with env["client"]() as c:
        await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
    user = await reset_password("admin", "reset-pass1")
    assert user.must_change_password is True
    async with env["client"]() as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "reset-pass1"})
        assert r.status_code == 200 and r.json()["must_change_password"] is True
        assert (await c.get("/api/admin/users")).status_code == 403


# --- registration ---------------------------------------------------------------------------


async def test_register_with_invite_creates_a_local_user(env):
    inv = await make_invite(env["admin"])
    assert inv["status"] == "active" and len(inv["code"]) == 9 and inv["code"][4] == "-"
    async with env["client"]() as c:
        r = await c.post(
            "/api/auth/register",
            json={"code": f" {inv['code'].replace('-', ' ').lower()} ", "username": "anna", "password": "secret1"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "anna" and not r.json()["is_admin"]
        assert (await c.get("/api/auth/me")).json()["username"] == "anna"
        # the code is spent
        r = await c.post("/api/auth/register", json={"code": inv["code"], "username": "anna2", "password": "secret1"})
        assert r.status_code == 400
        assert (await c.get("/api/admin/invites")).status_code == 403
        assert (await c.get("/api/admin/users")).status_code == 403
    listed = (await env["admin"].get("/api/admin/invites")).json()
    assert listed[0]["status"] == "used" and listed[0]["used_by"] == "anna"
    assert (await env["admin"].delete(f"/api/admin/invites/{inv['id']}")).status_code == 409
    # the account is local: the password is stored as an argon2id hash
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import User

    async with session_scope() as s:
        anna = (await s.execute(select(User).where(User.username == "anna"))).scalar_one()
        assert anna.password_hash and anna.password_hash.startswith("$argon2id$")


async def test_register_rejects_bad_input_and_keeps_the_code(env):
    inv = await make_invite(env["admin"])
    async with env["client"]() as c:
        for body, status in [
            ({"code": "WRONG-CODE", "username": "anna", "password": "secret1"}, 400),
            ({"code": inv["code"], "username": "ан на", "password": "secret1"}, 400),
            ({"code": inv["code"], "username": "anna", "password": "123"}, 400),
            ({"code": inv["code"], "username": "anna", "password": "password"}, 400),
            ({"code": inv["code"], "username": "Admin", "password": "secret1"}, 409),
        ]:
            r = await c.post("/api/auth/register", json=body)
            assert r.status_code == status, (body, r.text)
        # nothing above spent the code; Cyrillic names are fine
        r = await c.post("/api/auth/register", json={"code": inv["code"], "username": "Вера", "password": "secret1"})
        assert r.status_code == 200, r.text


async def test_expired_and_deleted_invites(env):
    from sqlalchemy import update

    from app.db import session_scope
    from app.models import Invite, utcnow

    old = await make_invite(env["admin"], "старое")
    gone = await make_invite(env["admin"], "отозванное")
    async with session_scope() as s:
        await s.execute(update(Invite).where(Invite.id == old["id"]).values(expires_at=utcnow() - timedelta(hours=1)))
    assert (await env["admin"].delete(f"/api/admin/invites/{gone['id']}")).json() == {"ok": True}
    statuses = {i["note"]: i["status"] for i in (await env["admin"].get("/api/admin/invites")).json()}
    assert statuses == {"старое": "expired"}
    async with env["client"]() as c:
        for inv in (old, gone):
            r = await c.post(
                "/api/auth/register", json={"code": inv["code"], "username": "anna", "password": "secret1"}
            )
            assert r.status_code == 400


async def test_guessing_codes_is_limited(env):
    async with env["client"]() as c:
        for _ in range(30):
            await c.post("/api/auth/register", json={"code": "AAAA-AAAA", "username": "x1", "password": "secret1"})
        r = await c.post("/api/auth/register", json={"code": "AAAA-AAAA", "username": "x1", "password": "secret1"})
        assert r.status_code == 429


# --- registration by email ------------------------------------------------------------------


@pytest.fixture
def letters(env, monkeypatch):
    """Email registration on, the letters it sends (no SMTP); `letters.deliver.fail` makes sending fail."""
    from app import config, mailer

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "homemusic@mail.test")
    config.get_settings.cache_clear()

    class Sent(list):
        deliver: Any = None

    sent = Sent()

    def deliver(msg):
        if getattr(deliver, "fail", False):
            raise OSError("connection refused")
        sent.append(msg)

    monkeypatch.setattr(mailer, "_deliver", deliver)
    sent.deliver = deliver
    return sent


def code_of(letter) -> str:
    import re

    found = re.search(r"\b(\d{6})\b", letter.get_content())
    assert found is not None
    return found.group(1)


def other_code(code: str) -> str:
    return "000000" if code != "000000" else "111111"


async def test_register_by_email(env, letters):
    async with env["client"]() as c:
        assert (await c.get("/api/auth/options")).json()["email_signup"] is True
        r = await c.post("/api/auth/email/code", json={"email": " Anna.K@Mail.test "})
        assert r.status_code == 200, r.text
        assert r.json() == {"resend_in": 60}
        letter = letters[-1]
        assert letter["To"] == "anna.k@mail.test" and letter["From"] == "HomeMusic <homemusic@mail.test>"
        code = code_of(letter)
        assert code in letter["Subject"]
        r = await c.post(
            "/api/auth/email/register",
            json={"email": "anna.k@mail.test", "code": other_code(code), "username": "anna", "password": "secret1"},
        )
        assert r.status_code == 400 and "осталось попыток — 4" in r.json()["detail"]
        # typed by hand: spaces in the code, the address in another case
        r = await c.post(
            "/api/auth/email/register",
            json={
                "email": "ANNA.K@mail.test",
                "code": f" {code[:3]} {code[3:]} ",
                "username": "anna",
                "password": "secret1",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "anna" and r.json()["email"] == "anna.k@mail.test"
        assert (await c.get("/api/auth/me")).json()["email"] == "anna.k@mail.test"
        # the letter is spent, and the address has an account now
        r = await c.post(
            "/api/auth/email/register",
            json={"email": "anna.k@mail.test", "code": code, "username": "anna2", "password": "secret1"},
        )
        assert r.status_code == 400
        assert (await c.post("/api/auth/email/code", json={"email": "anna.k@mail.test"})).status_code == 409
    async with env["client"]() as c:  # sign-in by the email instead of the name
        r = await c.post("/api/auth/login", json={"username": "Anna.K@Mail.test", "password": "secret1"})
        assert r.status_code == 200 and r.json()["username"] == "anna"
        r = await c.post("/api/auth/login", json={"username": "anna.k@mail.test", "password": "wrong1"})
        assert r.status_code == 401
    users = {u["username"]: u for u in (await env["admin"].get("/api/admin/users")).json()["users"]}
    assert users["anna"]["email"] == "anna.k@mail.test" and users["anna"]["invite_note"] is None


async def test_email_registration_keeps_the_code_on_bad_input(env, letters):
    async with env["client"]() as c:
        await c.post("/api/auth/email/code", json={"email": "gleb@mail.test"})
        code = code_of(letters[-1])
        for body, status in [
            ({"username": "ан на", "password": "secret1"}, 400),
            ({"username": "gleb", "password": "123"}, 400),
            ({"username": "Admin", "password": "secret1"}, 409),
        ]:
            r = await c.post("/api/auth/email/register", json={"email": "gleb@mail.test", "code": code, **body})
            assert r.status_code == status, (body, r.text)
        r = await c.post(
            "/api/auth/email/register",
            json={"email": "gleb@mail.test", "code": code, "username": "Глеб", "password": "secret1"},
        )
        assert r.status_code == 200, r.text


async def test_email_codes_are_limited(env, letters, monkeypatch):
    from sqlalchemy import select, update

    from app import config
    from app.db import session_scope
    from app.models import EmailCode, utcnow

    async def age(minutes: int) -> None:
        async with session_scope() as s:
            for row in (await s.execute(select(EmailCode))).scalars():
                row.created_at -= timedelta(minutes=minutes)

    def reg(email: str, code: str) -> dict:
        return {"email": email, "code": code, "username": "aa", "password": "secret1"}

    async with env["client"]() as c:
        for bad in ["", "anna", "anna@", "@mail.test", "an na@mail.test"]:
            assert (await c.post("/api/auth/email/code", json={"email": bad})).status_code == 400, bad
        assert (await c.post("/api/auth/email/code", json={"email": "a@mail.test"})).status_code == 200
        r = await c.post("/api/auth/email/code", json={"email": "a@mail.test"})
        assert r.status_code == 429 and "через" in r.json()["detail"]
        first = code_of(letters[-1])
        await age(2)
        assert (await c.post("/api/auth/email/code", json={"email": "a@mail.test"})).status_code == 200
        second = code_of(letters[-1])
        # only the latest letter counts: the first one's code is a wrong code now (attempt 1 of 5)
        wrong = first if first != second else other_code(second)
        assert (await c.post("/api/auth/email/register", json=reg("a@mail.test", wrong))).status_code == 400
        # five wrong codes kill the letter: the right one no longer works
        for _ in range(4):
            r = await c.post("/api/auth/email/register", json=reg("a@mail.test", other_code(second)))
        assert "Запросите новое письмо" in r.json()["detail"]
        assert (await c.post("/api/auth/email/register", json=reg("a@mail.test", second))).status_code == 400
        # at most five letters to an address an hour
        for _ in range(3):
            await age(2)
            assert (await c.post("/api/auth/email/code", json={"email": "a@mail.test"})).status_code == 200
        await age(2)
        r = await c.post("/api/auth/email/code", json={"email": "a@mail.test"})
        assert r.status_code == 429 and "через час" in r.json()["detail"]
        # an expired code
        assert (await c.post("/api/auth/email/code", json={"email": "b@mail.test"})).status_code == 200
        code = code_of(letters[-1])
        async with session_scope() as s:
            await s.execute(
                update(EmailCode)
                .where(EmailCode.email == "b@mail.test")
                .values(expires_at=utcnow() - timedelta(minutes=1))
            )
        r = await c.post("/api/auth/email/register", json=reg("b@mail.test", code))
        assert r.status_code == 400 and "устарел" in r.json()["detail"]
        # a letter that did not go out: no wait for another one
        letters.deliver.fail = True
        assert (await c.post("/api/auth/email/code", json={"email": "c@mail.test"})).status_code == 502
        letters.deliver.fail = False
        assert (await c.post("/api/auth/email/code", json={"email": "c@mail.test"})).status_code == 200
        # the daily limit of the mailbox
        monkeypatch.setenv("EMAIL_DAILY_LIMIT", "7")  # as many as went out above
        config.get_settings.cache_clear()
        r = await c.post("/api/auth/email/code", json={"email": "d@mail.test"})
        assert r.status_code == 503 and "завтра" in r.json()["detail"]
    assert len(letters) == 7


async def test_email_registration_needs_smtp(env):
    async with env["client"]() as c:
        assert (await c.post("/api/auth/email/code", json={"email": "a@mail.test"})).status_code == 404
        r = await c.post(
            "/api/auth/email/register",
            json={"email": "a@mail.test", "code": "123456", "username": "aa", "password": "secret1"},
        )
        assert r.status_code == 404


async def test_old_email_codes_are_purged(env, letters):
    from sqlalchemy import func, select

    from app.accounts import purge_email_codes
    from app.db import session_scope
    from app.models import EmailCode

    async with env["client"]() as c:
        for e in ("a@mail.test", "b@mail.test"):
            await c.post("/api/auth/email/code", json={"email": e})
    async with session_scope() as s:
        old = (await s.execute(select(EmailCode).where(EmailCode.email == "a@mail.test"))).scalar_one()
        old.created_at -= timedelta(days=3)
    assert await purge_email_codes() == 1
    async with session_scope() as s:
        assert (await s.execute(select(func.count(EmailCode.id)))).scalar_one() == 1


# --- OIDC -----------------------------------------------------------------------------------


async def test_options_list_oidc_providers(env):
    async with env["client"]() as c:
        out = (await c.get("/api/auth/options")).json()
    assert out["oidc_providers"] == [{"name": "google", "title": "Google"}, {"name": "fake", "title": "Fake"}]


async def test_oidc_start_redirects_with_pkce_state_and_nonce(env):
    async with env["client"]() as c:
        r = await c.get("/api/auth/oidc/fake/start")
        assert r.status_code == 302, r.text
        location = r.headers["location"]
        assert location.startswith(f"{IDP}/authorize?")
        assert "code_challenge_method=S256" in location and "state=" in location and "nonce=" in location
        pending = pending_cookie(c)
        assert pending["provider"] == "fake" and pending["mode"] == "login"


async def test_oidc_sign_in_of_a_linked_account(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import User

    async with env["client"]() as c:
        await c.get("/api/auth/oidc/fake/start")
        pending = pending_cookie(c)
        env["mock"].post(f"{IDP}/token").mock(
            return_value=httpx.Response(200, json={"id_token": idp_token(nonce=pending["nonce"])})
        )
        await c.get("/api/auth/oidc/fake/callback", params={"code": "abc", "state": pending["state"]})
        # the fake identity is new: no invite, so 409 and no session
        r = await c.get("/api/auth/oidc/fake/callback", params={"code": "abc", "state": pending["state"]})
        assert r.status_code in (400, 409)
    async with session_scope() as s:
        assert (await s.execute(select(User.id).where(User.username == "gleb"))).first() is None


async def test_oidc_with_an_invite_creates_a_local_user_then_signs_in(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import OAuthLink, User

    inv = await make_invite(env["admin"])
    async with env["client"]() as c:
        await c.get("/api/auth/oidc/fake/start", params={"invite": inv["code"]})
        pending = pending_cookie(c)
        env["mock"].post(f"{IDP}/token").mock(
            return_value=httpx.Response(200, json={"id_token": idp_token(nonce=pending["nonce"])})
        )
        r = await c.get("/api/auth/oidc/fake/callback", params={"code": "abc", "state": pending["state"]})
        assert r.status_code == 303, r.text  # back to the PWA, the cookies are set
        me = (await c.get("/api/auth/me")).json()
        assert me["username"] == "gleb" and me["oidc_links"] == ["fake"] and me["email"] == "gleb@mail.test"
    async with session_scope() as s:
        user = (await s.execute(select(User).where(User.username == "gleb"))).scalar_one()
        link = (await s.execute(select(OAuthLink).where(OAuthLink.user_id == user.id))).scalar_one()
        assert (link.provider, link.issuer, link.subject) == ("fake", IDP, "idp-1")
    # a second sign-in needs no invite
    async with env["client"]() as c:
        await c.get("/api/auth/oidc/fake/start")
        pending = pending_cookie(c)
        env["mock"].post(f"{IDP}/token").mock(
            return_value=httpx.Response(200, json={"id_token": idp_token(nonce=pending["nonce"])})
        )
        r = await c.get("/api/auth/oidc/fake/callback", params={"code": "abc", "state": pending["state"]})
        assert r.status_code == 303, r.text
        assert (await c.get("/api/auth/me")).json()["username"] == "gleb"


async def test_oidc_rejects_a_mismatched_state_and_a_bad_nonce(env):
    async with env["client"]() as c:
        await c.get("/api/auth/oidc/fake/start")
        pending = pending_cookie(c)
        r = await c.get("/api/auth/oidc/fake/callback", params={"code": "abc", "state": "other"})
        assert r.status_code == 400
        env["mock"].post(f"{IDP}/token").mock(
            return_value=httpx.Response(200, json={"id_token": idp_token(nonce="not-the-nonce")})
        )
        r = await c.get("/api/auth/oidc/fake/callback", params={"code": "abc", "state": pending["state"]})
        assert r.status_code == 400
        assert (await c.get("/api/auth/me")).status_code == 401


async def test_oidc_discovery_outage_is_502(env):
    env["mock"].get(f"{IDP}/.well-known/openid-configuration").mock(return_value=httpx.Response(503))
    async with env["client"]() as c:
        r = await c.get("/api/auth/oidc/fake/start")
        assert r.status_code == 502 and "не отвечает" in r.json()["detail"]


async def test_oidc_link_and_unlink(env):
    env["mock"].post(f"{IDP}/token").mock(
        return_value=httpx.Response(200, json={"id_token": idp_token(sub="idp-admin", email="boss@mail.test")})
    )
    async with env["client"]() as c:
        await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        await c.get("/api/auth/oidc/fake/start", params={"mode": "link"})
        pending = pending_cookie(c)
        env["mock"].post(f"{IDP}/token").mock(
            return_value=httpx.Response(
                200, json={"id_token": idp_token(sub="idp-admin", email="boss@mail.test", nonce=pending["nonce"])}
            )
        )
        r = await c.get("/api/auth/oidc/fake/callback", params={"code": "abc", "state": pending["state"]})
        assert r.status_code == 303, r.text
        assert (await c.get("/api/auth/me")).json()["oidc_links"] == ["fake"]
    # a link that belongs to another user cannot be taken over
    inv = await make_invite(env["admin"])
    async with env["client"]() as c:
        await c.post("/api/auth/register", json={"code": inv["code"], "username": "anna", "password": "secret1"})
        await c.get("/api/auth/oidc/fake/start", params={"mode": "link"})
        pending = pending_cookie(c)
        env["mock"].post(f"{IDP}/token").mock(
            return_value=httpx.Response(200, json={"id_token": idp_token(sub="idp-admin", nonce=pending["nonce"])})
        )
        r = await c.get("/api/auth/oidc/fake/callback", params={"code": "abc", "state": pending["state"]})
        assert r.status_code == 409
        r = await c.delete("/api/auth/oidc/link/fake")
        assert r.status_code == 404  # anna has no such link


async def test_oidc_unlink_is_refused_when_it_is_the_only_way_in(env):
    inv = await make_invite(env["admin"])
    async with env["client"]() as c:
        await c.get("/api/auth/oidc/fake/start", params={"invite": inv["code"]})
        pending = pending_cookie(c)
        env["mock"].post(f"{IDP}/token").mock(
            return_value=httpx.Response(200, json={"id_token": idp_token(nonce=pending["nonce"])})
        )
        assert (
            await c.get("/api/auth/oidc/fake/callback", params={"code": "abc", "state": pending["state"]})
        ).status_code == 303
        r = await c.delete("/api/auth/oidc/link/fake")
        assert r.status_code == 409
        # with a password of its own the link may go
        assert (
            await c.post("/api/auth/password", json={"current_password": None, "new_password": "own-pass1"})
        ).status_code == 200
        r = await c.delete("/api/auth/oidc/link/fake")
        assert r.status_code == 200 and r.json()["oidc_links"] == []


# --- the legacy Google ID-token flow ---------------------------------------------------------


async def test_google_sign_in_registers_with_invite_then_signs_in(env):
    async with env["client"]() as c:
        r = await c.post("/api/auth/google", json={"id_token": google_token()})
        assert r.status_code == 409, r.text  # not linked, no code
        inv = await make_invite(env["admin"])
        r = await c.post("/api/auth/google", json={"id_token": google_token(), "code": inv["code"]})
        assert r.status_code == 200, r.text
        out = r.json()
        # the name comes from the email when the app sends none; no password is returned
        assert out["username"] == "anna.k" and out["google_email"] == "anna.k@gmail.com"
        assert "password" not in out
    async with env["client"]() as c:  # another phone: the same account, no code needed
        r = await c.post("/api/auth/google", json={"id_token": google_token()})
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "anna.k" and "password" not in r.json()


async def test_google_tokens_are_checked(env):
    inv = await make_invite(env["admin"])
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    good = google_token()
    head, body, _ = good.split(".")
    forged = f"{head}.{body}.{_b64(other.sign(f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"
    async with env["client"]() as c:
        for token in [
            forged,
            google_token(aud="someone-else"),
            google_token(exp=int(time.time()) - 3600),
            google_token(iss="evil.example"),
            google_token(email_verified=False),
            "not-a-token",
        ]:
            r = await c.post("/api/auth/google", json={"id_token": token, "code": inv["code"]})
            assert r.status_code == 401, (token, r.text)
    statuses = {i["id"]: i["status"] for i in (await env["admin"].get("/api/admin/invites")).json()}
    assert statuses[inv["id"]] == "active"


async def test_link_google_to_a_password_account(env):
    admin = env["admin"]
    r = await admin.post(
        "/api/auth/google/link", json={"id_token": google_token(sub="g-admin", email="boss@gmail.com")}
    )
    assert r.status_code == 200 and r.json()["google_email"] == "boss@gmail.com"
    async with env["client"]() as c:
        r = await c.post("/api/auth/google", json={"id_token": google_token(sub="g-admin")})
        assert r.status_code == 200 and r.json()["username"] == "admin" and r.json()["is_admin"]
    # the same Google account can't go to a second user
    inv = await make_invite(admin)
    async with env["client"]() as c:
        r = await c.post("/api/auth/register", json={"code": inv["code"], "username": "anna", "password": "secret1"})
        assert r.status_code == 200
        r = await c.post("/api/auth/google/link", json={"id_token": google_token(sub="g-admin")})
        assert r.status_code == 409
    assert (await admin.delete("/api/auth/google/link")).json()["google_email"] is None
    async with env["client"]() as c:
        assert (await c.post("/api/auth/google", json={"id_token": google_token(sub="g-admin")})).status_code == 409


async def test_google_account_survives_a_password_change(env):
    await env["admin"].post("/api/auth/google/link", json={"id_token": google_token(sub="g-admin")})
    assert (
        await env["admin"].post("/api/auth/password", json={"current_password": "adminpw", "new_password": "adminpw2"})
    ).status_code == 200
    async with env["client"]() as c:
        # the Google link is a way in of its own now: the password change did not break it
        r = await c.post("/api/auth/google", json={"id_token": google_token(sub="g-admin")})
        assert r.status_code == 200 and r.json()["username"] == "admin"


# --- the admin list ----------------------------------------------------------------------------


async def test_admin_user_list_has_only_local_users(env):
    inv = await make_invite(env["admin"], "Анна")
    async with env["client"]() as c:
        await c.post("/api/auth/register", json={"code": inv["code"], "username": "anna", "password": "secret1"})
    users = (await env["admin"].get("/api/admin/users")).json()["users"]
    assert {u["username"] for u in users} == {"admin", "anna"}
    assert all(u["in_hub"] and u["id"] is not None for u in users)
    assert users[0]["starred_songs"] == 0 and users[0]["playlists"] == 0


async def test_search_history_is_shared_between_sessions_and_isolated_between_accounts(env):
    from app.events import bus

    inv = await make_invite(env["admin"], "Борис")
    async with env["client"]() as device, env["client"]() as other:
        assert (
            await device.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        ).status_code == 200
        await other.post("/api/auth/register", json={"code": inv["code"], "username": "boris", "password": "borispw"})
        me = (await env["admin"].get("/api/auth/me")).json()
        other_me = (await other.get("/api/auth/me")).json()
        own_events = bus.subscribe(me["id"])
        other_events = bus.subscribe(other_me["id"])
        try:
            r = await env["admin"].post("/api/search/history", json={"query": "  Кино  "})
            assert r.status_code == 200, r.text
            assert json.loads(own_events.get_nowait()) == {"event": "search_history", "data": {}}
            assert other_events.empty()
            r = await device.get("/api/search/history")
            assert r.status_code == 200, r.text
            assert r.headers["cache-control"] == "no-store"
            assert r.json() == {"queries": ["Кино"]}
            assert (await other.get("/api/search/history")).json() == {"queries": []}
            r = await other.post("/api/search/history", json={"query": "Земфира"})
            assert r.status_code == 200, r.text
            r = await device.delete("/api/search/history")
            assert r.status_code == 200, r.text
            assert (await env["admin"].get("/api/search/history")).json() == {"queries": []}
            assert (await other.get("/api/search/history")).json() == {"queries": ["Земфира"]}
            assert json.loads(own_events.get_nowait()) == {"event": "search_history", "data": {}}
        finally:
            bus.unsubscribe(me["id"], own_events)
            bus.unsubscribe(other_me["id"], other_events)


async def test_search_history_keeps_thirty_recent_queries_and_moves_repeats_to_top(env):
    from app.db import session_scope
    from app.models import SearchHistory, utcnow

    admin = env["admin"]
    user_id = (await admin.get("/api/auth/me")).json()["id"]
    now = utcnow()
    async with session_scope() as s:
        s.add_all(
            SearchHistory(
                user_id=user_id,
                query_key=f"запрос {i}",
                query=f"Запрос {i}",
                searched_at=now - timedelta(seconds=30 - i),
            )
            for i in range(30)
        )
    r = await admin.post("/api/search/history", json={"query": "Новый запрос"})
    assert r.status_code == 200, r.text
    queries = (await admin.get("/api/search/history")).json()["queries"]
    assert queries == ["Новый запрос", *[f"Запрос {i}" for i in range(29, 0, -1)]]
    r = await admin.post("/api/search/history", json={"query": "  ЗАПРОС 15  "})
    assert r.status_code == 200, r.text
    assert (await admin.get("/api/search/history")).json()["queries"] == [
        "ЗАПРОС 15",
        *[q for q in queries if q != "Запрос 15"],
    ]


@pytest.mark.parametrize("query", ["", " ", "я", " a ", "a" * 513])
async def test_search_history_rejects_invalid_queries(env, query):
    r = await env["admin"].post("/api/search/history", json={"query": query})
    assert r.status_code == 422, r.text
    assert (await env["admin"].get("/api/search/history")).json() == {"queries": []}


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_search_history_requires_authentication(env, method):
    async with env["client"]() as anonymous:
        r = await anonymous.request(method, "/api/search/history", json={"query": "Кино"})
        assert r.status_code == 401, r.text


async def test_admin_user_list_with_spending_and_songs(env):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import AiUsage, DownloadBatch, DownloadItem, Track, User, utcnow

    inv = await make_invite(env["admin"], "Анна")
    async with env["client"]() as c:
        await c.post("/api/auth/register", json={"code": inv["code"], "username": "anna", "password": "secret1"})
    async with session_scope() as s:
        admin = (await s.execute(select(User).where(User.username == "admin"))).scalar_one()
        anna = (await s.execute(select(User).where(User.username == "anna"))).scalar_one()
        s.add_all(
            [
                AiUsage(user_id=anna.id, kind="chat", tokens_in=1000, tokens_out=100, cost_usd=0.0015),
                AiUsage(user_id=anna.id, kind="images", tokens_in=5000, tokens_out=300, cost_usd=0.002),
                AiUsage(
                    user_id=anna.id,
                    kind="chat",
                    tokens_in=10,
                    tokens_out=10,
                    cost_usd=0.5,
                    created_at=utcnow() - timedelta(days=45),
                ),
                AiUsage(user_id=admin.id, kind="chat", tokens_in=10, tokens_out=10, cost_usd=0.01),
            ]
        )
        batch = DownloadBatch(user_id=anna.id, origin="search")
        s.add(batch)
        await s.flush()
        for status in ("done", "done", "failed", "queued"):
            s.add(
                DownloadItem(
                    batch_id=batch.id,
                    user_id=anna.id,
                    status=status,
                    query_json={"query": {"artist": "Кино", "title": "Группа крови"}},
                )
            )
        s.add(Track(uid="t-anna", path=None, artist="Кино", title="Группа крови", fingerprint="x", added_by=anna.id))
    out = (await env["admin"].get("/api/admin/users")).json()["users"]
    anna_out = next(u for u in out if u["username"] == "anna")
    assert anna_out["ai_cost_usd"] == 0.5035
    assert anna_out["ai_cost_30d_usd"] == 0.0035
    assert anna_out["ai_requests"] == 3
    assert anna_out["downloads_done"] == 2 and anna_out["downloads_failed"] == 1
    assert anna_out["downloads_active"] == 1 and anna_out["tracks_added"] == 1
    detail = (await env["admin"].get(f"/api/admin/users/{anna_out['id']}")).json()
    assert detail["ai_months"][0]["month"] == utcnow().strftime("%Y-%m")
    assert detail["recent_downloads"][0]["artist"] == "Кино"
    total = (await env["admin"].get("/api/admin/users")).json()
    assert total["ai_cost_usd"] == 0.5135 and total["library_tracks"] == 1


def test_password_hashes_are_argon2id_and_never_the_password():
    from app.passwords import hash_password, verify_password

    stored = hash_password("correct horse")
    assert stored.startswith("$argon2id$") and "correct horse" not in stored
    assert verify_password(stored, "correct horse")
    assert not verify_password(stored, "wrong")
    assert not verify_password(None, "correct horse")
    assert not verify_password("not-a-hash", "correct horse")


def test_password_hash_unit_roundtrip():
    from app.passwords import hash_password, verify_password

    stored = hash_password("s3cret-pass")
    assert verify_password(stored, "s3cret-pass")
    assert not verify_password(stored, "s3cret-pas")


def test_sha256_token_hash_is_stable():
    from app.security import hash_token

    assert hashlib.sha256(b"token").hexdigest() == hash_token("token")


# --- cookies, error format, secrets in logs, no OIDC Provider -------------------------------


async def test_cookie_attributes(env):
    transport = httpx.ASGITransport(app=env["app"])
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as plain:
        r = await plain.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 200
        cookies = {c.name: c for c in plain.cookies.jar}
        access = r.headers.get_list("set-cookie")
        assert any("hm_session=" in c and "HttpOnly" in c and "Path=/" in c for c in access)
        assert any("hm_refresh=" in c and "Path=/api/auth" in c and "HttpOnly" in c for c in access)
        assert any("hm_device=" in c and "Path=/api/auth" in c for c in access)
        assert all("SameSite=lax" in c for c in access if c.startswith(("hm_session", "hm_refresh")))
        # plain HTTP: the cookies are not Secure (a local network install without TLS keeps working)
        assert not any("Secure" in c for c in access)
        assert "hm_session" in cookies and "hm_refresh" in cookies and "hm_device" in cookies
    async with httpx.AsyncClient(transport=transport, base_url="https://hub.test") as tls:
        r = await tls.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 200
        assert all(
            "Secure" in c for c in r.headers.get_list("set-cookie") if c.startswith(("hm_session", "hm_refresh"))
        )


async def test_error_details_are_russian(env):
    import re

    cyrillic = re.compile(r"[А-Яа-яЁё]")
    async with env["client"]() as c:
        cases = [
            (await c.post("/api/auth/login", json={"username": "admin", "password": "nope"}), 401),
            (await c.get("/api/admin/users"), 401),
            (await c.delete("/api/auth/sessions/unknown"), 401),
        ]
        for response, status in cases:
            assert response.status_code == status, response.text
        await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        for _ in range(3):
            await c.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        limited = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert limited.status_code == 429
        inv = await make_invite(env["admin"])
        taken = await c.post(
            "/api/auth/register", json={"code": inv["code"], "username": "Admin", "password": "x1y2z3"}
        )
        assert taken.status_code == 409
    for response in (limited, taken):
        assert cyrillic.search(response.json()["detail"]), response.text


async def test_secrets_are_not_logged(env, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    async with env["client"]() as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 200
        refresh = c.cookies.get("hm_refresh", path="/api/auth")
        access = c.cookies.get("hm_session")
        assert (await c.post("/api/auth/refresh")).status_code == 200
    text = caplog.text
    for secret in ("adminpw", refresh, access):
        assert secret and secret not in text


async def test_hub_is_not_an_oidc_provider(env):
    async with env["client"]() as c:
        for path in (
            "/api/.well-known/openid-configuration",
            "/api/authorize",
            "/api/token",
            "/api/jwks.json",
        ):
            r = await c.get(path)
            assert r.status_code == 404, (path, r.status_code)
    paths = env["app"].openapi()["paths"]
    assert not any(p.startswith(("/authorize", "/token", "/jwks")) for p in paths)


# --- fixes after the advisor validation (see tasks.md, «Правки после валидации») -------------


async def test_login_limiter_counts_only_the_name_itself(env):
    async with env["client"]() as c:
        for _ in range(3):
            assert (await c.post("/api/auth/login", json={"username": "anna", "password": "nope"})).status_code == 401
        # anna is throttled now, the administrator is not: three failures of one name never lock others out
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.status_code == 200, r.text
        r = await c.post("/api/auth/login", json={"username": "anna", "password": "nope"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


async def test_login_with_a_very_long_name_is_not_a_server_error(env):
    async with env["client"]() as c:
        r = await c.post("/api/auth/login", json={"username": "x" * 5000, "password": "nope"})
        assert r.status_code in (401, 422), r.text


async def test_bootstrap_rejects_an_invalid_username(fresh_env):
    async with fresh_env["client"]() as c:
        # "@bootstrap" is the limiter's reserved key: it must never become a real account
        r = await c.post(
            "/api/auth/bootstrap",
            json={"token": "one-time-token", "username": "@bootstrap", "password": "boss-pass1"},
        )
        assert r.status_code == 400, r.text


async def test_bootstrap_with_a_non_ascii_token_is_401_not_500(fresh_env, monkeypatch):
    from app import config

    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "одноразовый-токен")
    config.get_settings.cache_clear()
    async with fresh_env["client"]() as c:
        r = await c.post(
            "/api/auth/bootstrap", json={"token": "неверный", "username": "boss", "password": "boss-pass1"}
        )
        assert r.status_code == 401, r.text
        r = await c.post(
            "/api/auth/bootstrap",
            json={"token": "одноразовый-токен", "username": "boss", "password": "boss-pass1"},
        )
        assert r.status_code == 200, r.text


async def test_unreadable_replacement_does_not_revoke_the_session(env):
    from sqlalchemy import update

    from app.db import session_scope
    from app.models import RefreshToken
    from app.security import hash_token

    async with env["client"]() as c:
        await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        old = c.cookies.get("hm_refresh", path="/api/auth")
        assert (await c.post("/api/auth/refresh")).status_code == 200
        current = c.cookies.get("hm_refresh", path="/api/auth")
        assert old and current and old != current
        async with session_scope() as s:  # as if HUB_SECRET_KEY had changed after the rotation
            await s.execute(
                update(RefreshToken).where(RefreshToken.id == hash_token(old)).values(replacement_enc="not-fernet")
            )
        c.cookies.set("hm_refresh", old, path="/api/auth", domain="hub.test")
        r = await c.post("/api/auth/refresh")
        assert r.status_code == 401, r.text
        # the operator's key rotation is not a reuse attack: the pair from that rotation still works
        c.cookies.set("hm_refresh", current, path="/api/auth", domain="hub.test")
        assert (await c.post("/api/auth/refresh")).status_code == 200


async def test_refresh_and_revoke_serialize_without_deadlock(env):
    import asyncio

    from app import tokens
    from app.db import session_scope
    from app.models import RefreshToken
    from app.security import hash_token

    async with env["client"]() as c:
        await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        refresh = c.cookies.get("hm_refresh", path="/api/auth")
        device = c.cookies.get("hm_device", path="/api/auth")
        assert refresh and device
    async with session_scope() as s:
        row = await s.get(RefreshToken, hash_token(refresh))
        assert row is not None
        family = row.family_id

    async def rotate_one():
        async with session_scope() as s:
            pair, _ = await tokens.rotate(s, refresh, device)
            return pair.refresh

    async def revoke_one():
        async with session_scope() as s:
            await tokens.revoke_family(s, family)
            return "revoked"

    results = await asyncio.gather(rotate_one(), revoke_one(), return_exceptions=True)
    for result in results:
        assert isinstance(result, str) or isinstance(result, tokens.RefreshError), repr(result)


async def test_sessions_hide_revoked_devices_and_report_the_last_use(env):
    async with env["client"]() as phone, env["client"]() as laptop:
        for c in (phone, laptop):
            assert (
                await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
            ).status_code == 200
        before = (await phone.get("/api/auth/sessions")).json()["sessions"]
        # the fixture's admin client, the phone and the laptop
        assert len(before) == 3
        assert (await phone.post("/api/auth/refresh")).status_code == 200
        after = (await phone.get("/api/auth/sessions")).json()["sessions"]
        current = next(s for s in after if s["current"])
        # a rotation is a use: the last use moves past the family's creation time
        assert current["last_used_at"] > current["created_at"]
        assert current["last_used_at"] >= next(s for s in before if s["current"])["last_used_at"]
        # a revoked device disappears from the list instead of staying as a dead row for 30 days
        label = f"Устройство {laptop.cookies.get('hm_device', path='/api/auth')[:6]}"
        other = next(s for s in after if s["device"] == label)
        assert (await phone.delete(f"/api/auth/sessions/{other['id']}")).json() == {"ok": True}
        left = (await phone.get("/api/auth/sessions")).json()["sessions"]
        assert len(left) == 2  # the fixture's client and the phone
        assert [s["current"] for s in left] == [True, False]
        assert (await laptop.post("/api/auth/refresh")).status_code == 401


async def test_concurrent_refresh_rotates_once(env):
    import asyncio

    from sqlalchemy import select

    from app import tokens
    from app.db import session_scope
    from app.models import RefreshToken
    from app.security import hash_token

    async with env["client"]() as c:
        await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        refresh = c.cookies.get("hm_refresh", path="/api/auth")
        device = c.cookies.get("hm_device", path="/api/auth")
        assert refresh and device

    async def one() -> str:
        async with session_scope() as s:
            pair, _ = await tokens.rotate(s, refresh, device)
            return pair.refresh

    first, second = await asyncio.gather(one(), one())
    # the row lock serializes them: the second request returns the pair the first one stored
    assert first == second
    async with session_scope() as s:
        rotated = await s.get(RefreshToken, hash_token(refresh))
        assert rotated is not None
        rows = list(
            (await s.execute(select(RefreshToken).where(RefreshToken.family_id == rotated.family_id))).scalars()
        )
    assert len([row for row in rows if row.revoked_at is None]) == 1


async def test_bootstrap_attempts_are_limited(fresh_env):
    from sqlalchemy import update

    from app.db import session_scope
    from app.models import LoginAttempt, utcnow

    body = {"username": "boss", "password": "boss-pass1"}
    async with fresh_env["client"]() as c:
        for _ in range(3):
            r = await c.post("/api/auth/bootstrap", json={**body, "token": "wrong"})
            assert r.status_code == 401, r.text
        r = await c.post("/api/auth/bootstrap", json={**body, "token": "one-time-token"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    async with session_scope() as s:  # the window passes: the right token still creates the administrator
        await s.execute(update(LoginAttempt).values(attempted_at=utcnow() - timedelta(minutes=5)))
    async with fresh_env["client"]() as c:
        r = await c.post("/api/auth/bootstrap", json={**body, "token": "one-time-token"})
        assert r.status_code == 200, r.text


async def test_options_skip_an_undecryptable_provider_secret(env, tmp_path, monkeypatch):
    from app import config as app_config

    cfg = tmp_path / "broken.yaml"
    cfg.write_text(
        f"""
oidc:
  providers:
    broken:
      issuer: https://broken.test
      client_id: broken-client
      client_secret_enc: not-a-fernet-token
    fake:
      issuer: {IDP}
      client_id: {OIDC_CLIENT}
      client_secret: {OIDC_SECRET}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(cfg))
    app_config.get_settings.cache_clear()
    async with env["client"]() as c:
        r = await c.get("/api/auth/options")
        assert r.status_code == 200, r.text
        names = [p["name"] for p in r.json()["oidc_providers"]]
        assert "broken" not in names and "fake" in names
        # the broken provider is gone, not a 500 for the whole sign-in screen
        assert (await c.get("/api/auth/oidc/broken/start")).status_code == 404


async def test_oidc_link_start_respects_the_password_gate(fresh_env):
    async with fresh_env["client"]() as c:
        assert (
            await c.post(
                "/api/auth/bootstrap", json={"token": "one-time-token", "username": "boss", "password": "boss-pass1"}
            )
        ).status_code == 200
        for mode in ("link", "login"):
            r = await c.get("/api/auth/oidc/anything/start", params={"mode": mode})
            assert r.status_code == 403, (mode, r.text)  # signed in, but the password must change first
    async with fresh_env["client"]() as anonymous:
        r = await anonymous.get("/api/auth/oidc/anything/start", params={"mode": "link"})
        assert r.status_code == 401, r.text
