"""
app/services/integration/crypto.py

Symmetric encryption for OrganizationIntegration/UserIntegration access &
refresh tokens at rest. Fernet (AES-128-CBC + HMAC, via `cryptography`) —
already a transitive dependency, no new package needed.

Deliberately does NOT fail at import time if INTEGRATION_ENCRYPTION_KEY is
unset — the app must still boot in environments that haven't configured
Google integration yet. It fails loudly the moment anything actually tries
to encrypt/decrypt a token instead.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from app.core.config import settings


class IntegrationNotConfigured(Exception):
    """Raised when a Google integration action is attempted before the
    backend has been given OAuth credentials / an encryption key."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if not settings.INTEGRATION_ENCRYPTION_KEY:
        raise IntegrationNotConfigured(
            "INTEGRATION_ENCRYPTION_KEY is not set — generate one with Fernet.generate_key() "
            "and add it to the backend's environment before connecting any integration."
        )
    return Fernet(settings.INTEGRATION_ENCRYPTION_KEY.encode())


def encrypt_token(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_token(cipher: str) -> str:
    try:
        return _fernet().decrypt(cipher.encode()).decode()
    except InvalidToken as exc:
        # The encryption key was rotated/changed since this token was
        # stored — surface as "reconnect required", not a 500.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Stored integration credentials could not be decrypted — please reconnect."
        ) from exc
