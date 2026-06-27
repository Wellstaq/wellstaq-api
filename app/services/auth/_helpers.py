"""
app/services/auth/_helpers.py

Internal helpers shared across the auth sub-services.
Not part of the public API — import from app.services.auth, not here directly.

Contents
─────────
  AuthError          — re-exported for convenience within this package
  _mask_email        — "john@x.com" → "j***@x.com"
  _create_user_settings
  _log_audit         — appends a UserActivityFeed row
  _load_pending_invite
  _decode_email_verification_token
  _decode_invite_verification_token
  _make_invite_verification_token
"""


from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import TokenError, decode_token
from app.core.constants import (
    ActivityType,
    InviteStatus,
    ReferenceType,
    TokenType,
)
from app.models import (
    OrganizationInvite,
    User,
    UserActivityFeed,
    UserSettings,
)
from .errors import AuthError


def _mask_email(email: str) -> str:
    """'john.doe@example.com' → 'j***@example.com'"""
    local, domain = email.rsplit("@", 1)
    return local[0] + "***@" + domain


def _create_user_settings(user: User) -> UserSettings:
    """
    Create default user settings.
    Onboarding status is tracked via User.status ('pending' vs 'active').
    """
    return UserSettings(
        user_id=user.id,
        organization_id=user.organization_id,
    )


def _log_audit(
    session,
    *,
    user: User,
    activity_type: ActivityType,
    reference_id: str | None = None,
    reference_type: ReferenceType | None = None,
    metadata: dict | None = None,
) -> None:
    """Append an immutable audit event to the activity feed."""
    session.add(UserActivityFeed(
        user_id=user.id,
        organization_id=user.organization_id,
        activity_type=activity_type,
        reference_id=reference_id or user.id,
        reference_type=reference_type or ReferenceType.USER,
        activity_metadata=metadata or {},
    ))


async def _load_pending_invite(
    session: AsyncSession,
    invite_code: str,
    now: datetime,
    *,
    load_permissions: bool = False,
) -> OrganizationInvite:
    """Load and validate a pending, unexpired invite."""
    q = select(OrganizationInvite).where(
        OrganizationInvite.invite_code == invite_code,
        OrganizationInvite.status == InviteStatus.PENDING,
    )
    if load_permissions:
        q = q.options(selectinload(OrganizationInvite.invite_permissions))

    result = await session.execute(q)
    invite = result.scalar_one_or_none()

    if not invite:
        raise AuthError("Invalid or already used invite code.", status.HTTP_404_NOT_FOUND)
    if invite.expires_at < now:
        raise AuthError("This invite has expired.", status.HTTP_410_GONE)
    return invite


def _decode_email_verification_token(raw_token: str) -> str:
    """Decode an email_verification_token, return the verified email."""
    try:
        payload = decode_token(raw_token)
    except TokenError as exc:
        raise AuthError("Invalid or expired email verification token.") from exc

    if payload.get("type") != TokenType.EMAIL_VERIFICATION:
        raise AuthError("Invalid or expired email verification token.")

    email = payload.get("email", "").lower().strip()
    if not email:
        raise AuthError("Invalid or expired email verification token.")

    return email


def _decode_invite_verification_token(raw_token: str) -> tuple[str, str]:
    """Decode an invite_verification_token, return (email, invite_code)."""
    try:
        payload = decode_token(raw_token)
    except TokenError as exc:
        raise AuthError("Invalid or expired invite verification token.") from exc

    if payload.get("type") != TokenType.INVITE_VERIFICATION:
        raise AuthError("Invalid or expired invite verification token.")

    email = payload.get("email", "").lower().strip()
    invite_code = payload.get("invite_code", "")

    if not email or not invite_code:
        raise AuthError("Invalid or expired invite verification token.")

    return email, invite_code


def _make_invite_verification_token(email: str, invite_code: str) -> str:
    """Build a 30-minute invite_verification_token."""
    from app.auth import jwt as _jwt
    return _jwt._create_token(
        subject=email,
        expires_delta=timedelta(minutes=30),
        token_type="access",
        extra_claims={
            "type": TokenType.INVITE_VERIFICATION,
            "email": email,
            "invite_code": invite_code,
        },
    )