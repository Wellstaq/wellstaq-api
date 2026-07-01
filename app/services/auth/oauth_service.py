"""
app/services/auth/oauth_service.py

OAuth login — account linking and login for existing users only.

Design rules (new auth flow):
  1. User must already exist — registered via password (org_owner or invite).
  2. User must be ACTIVE — PENDING means onboarding incomplete, SUSPENDED/INACTIVE blocked.
  3. If OAuth provider already linked → login immediately.
  4. If OAuth provider NOT linked:
       - Google: allowed only for Gmail addresses → auto-link then login.
       - Apple: not supported for auto-linking (Apple emails are often private relay
         addresses that don't match the account email).
  5. No new user creation via OAuth — registration is password-only.
"""

from __future__ import annotations

import logging

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import (
    ActivityType,
    OAuthProvider,
    UserStatus,
)
from app.models import User, UserAuthProvider
from .token_service import issue_tokens
from ._helpers import AuthError, _log_audit

logger = logging.getLogger(__name__)


def _is_gmail_domain(email: str) -> bool:
    """
    Return True if the email address is a Gmail address.
    Plain def — no I/O, no awaiting needed.
    """
    try:
        return email.split("@")[1].lower() == "gmail.com"
    except (IndexError, AttributeError):
        return False


async def _link_provider(
    session: AsyncSession,
    user: User,
    provider: str,
    claims: dict,
) -> None:
    """
    Add a new OAuth provider row for an existing user.
    No-op if already linked (idempotent).
    """
    existing = await session.execute(
        select(UserAuthProvider).where(
            UserAuthProvider.user_id == user.id,
            UserAuthProvider.provider == provider,
        )
    )
    if existing.scalar_one_or_none():
        return  # already linked

    session.add(UserAuthProvider(
        user_id=user.id,
        organization_id=user.organization_id,
        provider=provider,
        provider_user_id=claims.get("sub"),
        access_token=None,
        refresh_token=None,
    ))
    logger.info(
        "OAuth provider linked",
        extra={"user_id": str(user.id), "provider": provider},
    )


async def oauth_login(
    session: AsyncSession,
    provider: str,
    claims: dict,
    device_name: str | None = None,
    device_fingerprint: str | None = None,
    ip_address: str | None = None,
) -> dict:
    """
    OAuth login for existing users only. No registration path.

    Claims are pre-verified by the router (_verify_google / _verify_apple)
    and passed in directly — this function never calls any provider API.

    Parameters
    ──────────
    provider — "google" or "apple"
    claims   — verified token claims dict from the router

    Security notes
    ──────────────
    1. email_verified is enforced before any account lookup — prevents
       an unverified Google email from being used to take over an account
       via the Gmail auto-link flow.

    2. When a provider is already linked, claims["sub"] is compared against
       the stored provider_user_id. A mismatch means a different Google/Apple
       account is presenting a token for this email — deny and require re-link.

    3. First-link inserts are wrapped to handle IntegrityError from a race
       condition where two simultaneous requests both attempt to insert the
       same (user_id, provider) row. The second request continues normally
       since the link now exists.
    """
    provider = provider.lower()

    # ── FIX 1: enforce email_verified before any account lookup ───────────────
    # Google can issue ID tokens for unverified addresses (e.g. freshly created
    # accounts that haven't completed email verification). If we skip this check,
    # an attacker who registers a Google account with someone else's Gmail address
    # and catches it before that person verifies could trigger the auto-link flow
    # and gain access to the victim's Wellstaq account.
    if not claims.get("email_verified"):
        raise AuthError(
            "Your Google account email address has not been verified. "
            "Please verify your email with Google and try again.",
            status.HTTP_400_BAD_REQUEST,
        )

    email = claims.get("email", "").lower().strip()
    provider_sub = claims.get("sub", "")

    if not email or not provider_sub:
        raise AuthError(
            "OAuth token is missing required claims (email or sub).",
            status.HTTP_400_BAD_REQUEST,
        )

    avatar_url: str | None = claims.get("picture") if provider == OAuthProvider.GOOGLE else None

    # ── Load user + auth_providers in one query ────────────────────────────────
    # selectinload avoids the previous two-query pattern (user → then provider).
    result = await session.execute(
        select(User)
        .options(selectinload(User.auth_providers))
        .where(User.email == email)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise AuthError(
            "No account found for this email. Please register first using your email and password.",
            status.HTTP_404_NOT_FOUND,
        )

    # ── Status guard ───────────────────────────────────────────────────────────
    if user.status == UserStatus.PENDING:
        raise AuthError(
            "Your account registration is incomplete. Please finish onboarding first.",
            status.HTTP_403_FORBIDDEN,
        )
    if user.status in (UserStatus.SUSPENDED, UserStatus.INACTIVE):
        raise AuthError(
            "Your account is not active. Please contact support.",
            status.HTTP_403_FORBIDDEN,
        )

    # ── Check if provider already linked (from loaded relationship) ────────────
    provider_linked = next(
        (p for p in user.auth_providers if p.provider == provider),
        None,
    )

    if provider_linked:
        # ── FIX 2: verify sub matches stored provider_user_id ─────────────────
        # The email lookup already ties us to the right user, but a defence-in-
        # depth check ensures the token genuinely belongs to the linked account.
        # Mismatch means a different Google/Apple identity is presenting a token
        # for this email — deny until the user explicitly re-links.
        if provider_linked.provider_user_id != provider_sub:
            logger.warning(
                "OAuth sub mismatch — possible account hijack attempt",
                extra={
                    "user_id": str(user.id),
                    "provider": provider,
                    "stored_sub": provider_linked.provider_user_id,
                    "presented_sub": provider_sub,
                },
            )
            raise AuthError(
                "The linked account identity does not match this token. "
                "Please re-link your account from account settings.",
                status.HTTP_401_UNAUTHORIZED,
            )

    else:
        # ── Provider not linked — apply auto-link rules ────────────────────────
        if provider == OAuthProvider.GOOGLE:
            if not _is_gmail_domain(email):
                raise AuthError(
                    "Google login is only supported for Gmail addresses. "
                    "Please use your password to log in.",
                    status.HTTP_400_BAD_REQUEST,
                )
            # ── FIX 3: handle race condition on first link ─────────────────────
            # Two simultaneous Google logins from the same user could both reach
            # this branch and attempt to insert the same (user_id, provider) row.
            # If the DB has a unique constraint on (user_id, provider), the second
            # insert raises IntegrityError. We catch it and continue — the link
            # now exists thanks to the first request, so login can proceed.
            try:
                await _link_provider(session, user, provider, claims)
                await session.flush()
            except IntegrityError:
                await session.rollback()
                logger.info(
                    "OAuth provider link race condition resolved",
                    extra={"user_id": str(user.id), "provider": provider},
                )
                # Re-load user after rollback so the session is clean
                result = await session.execute(
                    select(User)
                    .options(selectinload(User.auth_providers))
                    .where(User.id == user.id)
                )
                user = result.scalar_one()

        elif provider == OAuthProvider.APPLE:
            raise AuthError(
                "No Apple account linked. Please link Apple Sign In from your account settings first.",
                status.HTTP_400_BAD_REQUEST,
            )
        else:
            raise AuthError(
                f"Unsupported OAuth provider '{provider}'.",
                status.HTTP_400_BAD_REQUEST,
            )

    # ── Update avatar ──────────────────────────────────────────────────────────
    if avatar_url and user.avatar_url != avatar_url:
        user.avatar_url = avatar_url

    # ── Audit + flush ──────────────────────────────────────────────────────────
    _log_audit(
        session,
        user=user,
        activity_type=ActivityType.LOGIN,
        metadata={"provider": provider},
    )
    await session.flush()

    return await issue_tokens(session, user, device_name, device_fingerprint, ip_address)