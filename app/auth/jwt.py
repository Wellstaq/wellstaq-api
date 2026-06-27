
"""
app/auth/jwt.py  (updated)

JWT token creation and verification.

Access tokens are short-lived (24h) and carry embedded claims:
  sub, org_id, branch_id, role, permissions

Refresh tokens are long-lived (30 days), carry only sub + type,
and are stored hashed in UserDevice.refresh_token — so they can be
individually revoked per device without invalidating all sessions.

Permission embedding strategy:
  Permissions are loaded at login time and embedded as a list of
  permission name strings in the access token. This means:
  - Zero DB queries per request for permission checks (fast)
  - Permission changes take effect when the access token expires
    (max 24h lag) — acceptable for a B2B wellness platform
  - On token refresh, permissions are re-loaded from DB, so a
    revocation propagates within 24h worst case

Hash strategy for stored refresh tokens:
  The raw refresh token is sent to the client. The stored value in
  UserDevice.refresh_token is a SHA-256 hex digest of it — so a
  DB breach doesn't expose usable tokens.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import JWTError, jwt

from app.core.config import settings


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or invalid."""


def _create_token(
    subject: str,
    expires_delta: timedelta,
    token_type: Literal["access", "refresh"],
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(
    user_id: str,
    organization_id: str,
    branch_id: str | None,
    role: str,
    permissions: list[str],
) -> str:
    """
    Create a short-lived access token with embedded identity + permissions.

    All permission checks in the service layer should read from the
    token's 'permissions' claim rather than querying the DB per request.
    """
    return _create_token(
        subject=user_id,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        token_type="access",
        extra_claims={
            "org_id": organization_id,
            "branch_id": branch_id,
            "role": role,
            "permissions": permissions,
        },
    )


def create_refresh_token(user_id: str) -> str:
    """
    Create a long-lived refresh token. Only sub + type embedded —
    no permissions or org claims. Store the hashed version in DB,
    send the raw version to the client.
    """
    return _create_token(
        subject=user_id,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        token_type="refresh",
    )


def hash_refresh_token(raw_token: str) -> str:
    """SHA-256 hex digest of a refresh token for safe DB storage."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a token, raising TokenError if invalid or expired."""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as exc:
        raise TokenError(str(exc)) from exc
