"""Password hashing (argon2id) and the password policy of local Hub accounts."""

from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException

# argon2id, parameters of the library's defaults (RFC 9106's second recommended option)
_hasher = PasswordHasher()

MIN_PASSWORD = 6

# the passwords a guesser tries first; the policy rejects them whatever the account is
WEAK_PASSWORDS = {
    "password",
    "passw0rd",
    "qwerty",
    "qwerty123",
    "123456",
    "1234567",
    "12345678",
    "123456789",
    "1234567890",
    "admin",
    "admin123",
    "administrator",
    "iloveyou",
    "letmein",
    "welcome",
    "monkey",
    "dragon",
    "football",
    "homemusic",
}


def hash_password(password: str) -> str:
    """The argon2id hash of the password: the only form the Hub ever stores."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """True when the password matches the stored argon2id hash; a broken or empty hash never matches."""
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str | None) -> bool:
    return bool(password_hash) and _hasher.check_needs_rehash(password_hash)  # type: ignore[arg-type]


def check_password_policy(username: str, password: str) -> None:
    """Raises 400 with a Russian message when the password is too weak for a Hub account."""
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Пароль — не короче {MIN_PASSWORD} символов")
    if password.strip().lower() in WEAK_PASSWORDS:
        raise HTTPException(400, "Такой пароль слишком простой: выберите другой")
    if password.strip().lower() == username.strip().lower():
        raise HTTPException(400, "Пароль не должен совпадать с именем пользователя")
    if not re.search(r"[^\d]", password):
        raise HTTPException(400, "Пароль не должен состоять только из цифр")
