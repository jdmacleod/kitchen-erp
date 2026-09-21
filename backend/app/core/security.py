"""Password hashing and opaque token generation."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with the library's current defaults

API_TOKEN_PREFIX = "kerp_"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def new_api_token() -> str:
    return API_TOKEN_PREFIX + secrets.token_urlsafe(32)


def digest(secret: str) -> str:
    """Stored form of a session or API token. A database leak yields nothing usable."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
