"""
JWT token creation and verification.

Access tokens are short-lived and sent on every request.
Refresh tokens are longer-lived and used only to mint new access tokens.

This module has no FastAPI-specific code — it's pure token logic,
kept separate from app.auth.dependencies (which wires this into routes).
"""

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


def create_access_token(user_id: str, extra_claims: dict[str, Any] | None = None) -> str:
    """
    Create a short-lived access token for a given user id (subject).

    When implementing login (app/routers/auth.py), call this with
    extra_claims={"role": user.role, "organization_id": str(user.organization_id),
    "branch_id": str(user.branch_id) if user.branch_id else None} — these
    three claims are what app/auth/scoping.py reads to resolve the
    requester's role and scope on every protected route, without an
    extra database round-trip per request.
    """
    return _create_token(
        subject=user_id,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        token_type="access",
        extra_claims=extra_claims,
    )


def create_refresh_token(user_id: str) -> str:
    """Create a long-lived refresh token for a given user id (subject)."""
    return _create_token(
        subject=user_id,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        token_type="refresh",
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a token, raising TokenError if invalid or expired."""
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise TokenError(str(exc)) from exc
