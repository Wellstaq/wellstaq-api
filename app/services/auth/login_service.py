"""
app/services/auth/login_service.py

Password login only.
OAuth login has moved to oauth_service.py per the new design:
  OAuth is account-linking for existing users, not a registration path.
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.security import verify_password
from app.core.constants import (
    ActivityType,
    OAuthProvider,
    UserStatus,
)
from app.models import User
from .token_service import issue_tokens
from .two_factor_service import issue_two_fa_challenge
from ._helpers import (
    AuthError,
    _log_audit,
)

import bcrypt

# A syntactically valid bcrypt hash with no real corresponding password.
# Generated once at import time so verify_password's timing is identical
# whether or not the email exists (anti-enumeration defense).
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt()).decode()

async def password_login(
    session: AsyncSession,
    email: str,
    password: str,
    device_name: str | None = None,
    device_fingerprint: str | None = None,
    ip_address: str | None = None,
) -> dict:
    """
    Authenticate with email + password.

    Returns TokenResponse (2FA off) or TwoFaChallengeResponse (2FA on).

    Constant-time verify: always runs bcrypt even when user is None to
    prevent timing attacks that reveal whether an email is registered.

    Single query: User + settings + auth_providers loaded together to
    avoid the two-query pattern (user then provider check).
    """
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.settings),
            selectinload(User.auth_providers),
            selectinload(User.organization),
        )
        .where(User.email == email.lower())
    )
    user = result.scalar_one_or_none()
    stored_hash = user.hashed_password if user else _DUMMY_HASH
    password_ok = verify_password(password, stored_hash)

    if not password_ok or user is None:
        # Audit failed login when user exists only — logging for a non-existent
        # email would confirm the address isn't registered (enumeration attack).
        if user is not None:
            await _log_audit(
                session,
                user=user,
                activity_type=ActivityType.FAILED_LOGIN,
                metadata={"reason": "wrong_password"},
            )
            await session.flush()
        raise AuthError("Incorrect email or password.", status.HTTP_401_UNAUTHORIZED)

    if user.status == UserStatus.SUSPENDED:
        raise AuthError(
            "Your account has been suspended. Contact your administrator.",
            status.HTTP_403_FORBIDDEN,
        )
    if user.status == UserStatus.INACTIVE:
        raise AuthError(
            "Your account is inactive. Contact your administrator.",
            status.HTTP_403_FORBIDDEN,
        )
    if user.status == UserStatus.PENDING:
        raise AuthError(
            "Your account registration is incomplete. Please finish onboarding.",
            status.HTTP_403_FORBIDDEN,
        )
    if user.organization is not None and not user.organization.is_active:
        raise AuthError(
            "Your organization's account has been suspended. Contact Wellstaq support.",
            status.HTTP_403_FORBIDDEN,
        )

    # Provider check from the already-loaded relationship — no second query.
    has_password_provider = any(
        p.provider == OAuthProvider.PASSWORD for p in user.auth_providers
    )
    if not has_password_provider:
        raise AuthError(
            "This account uses social login. Please sign in with Google or Apple.",
            status.HTTP_400_BAD_REQUEST,
        )

    settings_row = user.settings
    if settings_row and settings_row.two_fa_enabled:
        return await issue_two_fa_challenge(
            session=session,
            user=user,
            method=settings_row.two_fa_method,
            device_name=device_name,
            device_fingerprint=device_fingerprint,
            ip_address=ip_address,
        )

    await _log_audit(
        session,
        user=user,
        activity_type=ActivityType.LOGIN,
        metadata={"provider": OAuthProvider.PASSWORD},
    )
    await session.flush()

    return await issue_tokens(session, user, device_name, device_fingerprint, ip_address)
