"""
app/services/auth/invite_service.py

Invite-based registration flows:
  1. request_invite_otp  — validate invite, send OTP
  2. verify_invite_otp   — verify OTP, return invite_verification_token
  3. invite_register     — complete registration with wellbeing baseline

Employee registration flow:
  - User created with status=PENDING
  - Wellbeing baseline + priorities recorded
  - User activated (ACTIVE) atomically before tokens issued
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.core.constants import (
    ActivityType,
    InviteStatus,
    OAuthProvider,
    ReferenceType,
    UserStatus,
)
from app.models import (
    User,
    UserAuthProvider,
    UserPermission,
    UserPriority,
    UserWellbeingBaseline,
)
from .token_service import issue_tokens
from ._helpers import (
    AuthError,
    _create_user_settings,
    _decode_invite_verification_token,
    _load_pending_invite,
    _log_audit,
    _make_invite_verification_token,
    _mask_email,
)


async def request_invite_otp(session: AsyncSession, invite_code: str) -> str:
    """
    Validate the invite and send an OTP to the invite's email.
    Returns masked email: "j***@example.com".
    """
    from .otp_service import request_otp

    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(session, invite_code, now)

    existing = await session.execute(
        select(User.id).where(User.email == invite.invited_email.lower())
    )
    if existing.scalar_one_or_none():
        raise AuthError(
            "An account already exists for this email address.",
            status.HTTP_409_CONFLICT,
        )

    await request_otp(session, invite.invited_email, purpose="verification")
    return _mask_email(invite.invited_email)


async def verify_invite_otp(
    session: AsyncSession, invite_code: str, code: str
) -> str:
    """
    Verify the OTP for an invite. Returns an invite_verification_token.

    OtpError propagates to the router (which must commit() to persist
    attempt_count before re-raising — handled by request_guard).

    FIX: previously returned _mask_email() (wrong). Must return the
    invite_verification_token so the client can pass it to invite_register().
    """
    from .otp_service import verify_otp

    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(session, invite_code, now)
    await verify_otp(session, invite.invited_email, code)
    # Return the token, not the masked email — the client needs this for step 3
    return _make_invite_verification_token(invite.invited_email, invite_code)


async def invite_register(
    session: AsyncSession,
    invite_verification_token: str,
    first_name: str,
    last_name: str,
    password: str,
    country: str,
    state: str,
    baseline: dict,
    priorities: list[dict],
    device_name: str | None = None,
    device_fingerprint: str | None = None,
    ip_address: str | None = None,
) -> dict:
    """
    Complete invite registration with wellbeing baseline.

    Email and invite_code decoded from invite_verification_token —
    client cannot supply either (prevents invite-squatting and
    token substitution attacks).

    Flow:
      1. Decode token → email, invite_code
      2. Re-validate invite
      3. Token substitution guard
      4. Duplicate email guard
      5. Create user (PENDING — not yet active)
      6. Ancillary records: auth provider, settings, permissions
      7. Wellbeing baseline rows
      8. Priority rows
      9. Activate user (ACTIVE) + mark invite accepted + audit
      10. Issue tokens
    """
    # ── Step 1 ─────────────────────────────────────────────────────────────────
    email, invite_code = _decode_invite_verification_token(invite_verification_token)

    # ── Step 2 ─────────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(
        session, invite_code, now, load_permissions=True
    )

    # ── Step 3 ─────────────────────────────────────────────────────────────────
    if invite.invited_email.lower() != email:
        raise AuthError("Invalid or expired invite verification token.")

    # ── Step 4 ─────────────────────────────────────────────────────────────────
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise AuthError("An account already exists for this email.", status.HTTP_409_CONFLICT)

    # ── Step 5: create user as PENDING ─────────────────────────────────────────
    user = User(
        first_name=first_name,
        last_name=last_name,
        email=email,
        hashed_password=hash_password(password),
        organization_id=invite.organization_id,
        branch_id=invite.branch_id,
        department_id=invite.invited_department_id,
        role_id=invite.invited_role_id,
        country=country,
        state=state,
        status=UserStatus.PENDING,  # activated in step 9 after all data is staged
    )
    session.add(user)
    await session.flush()  # need user.id for child records

    # ── Step 6: ancillary records ──────────────────────────────────────────────
    session.add(UserAuthProvider(
        user_id=user.id,
        organization_id=user.organization_id,
        provider=OAuthProvider.PASSWORD,
        provider_user_id=None,
        access_token=None,
        refresh_token=None,
    ))
    session.add(_create_user_settings(user))

    for ip in invite.invite_permissions:
        session.add(UserPermission(
            user_id=user.id,
            permission_id=ip.permission_id,
            organization_id=ip.organization_id,
            branch_id=ip.branch_id,
            granted_by=invite.invited_by,
        ))

    # ── Step 7: wellbeing baseline ─────────────────────────────────────────────
    if "entries" not in baseline:
        raise AuthError("Invalid baseline format.", status.HTTP_400_BAD_REQUEST)

    recorded = datetime.now(timezone.utc)
    for entry in baseline["entries"]:
        session.add(UserWellbeingBaseline(
            user_id=user.id,
            organization_id=user.organization_id,
            dimension=entry["dimension"],
            level=entry["level"],
            reason=entry["reason"],
            recorded_at=recorded,
        ))

    # ── Step 8: priorities ─────────────────────────────────────────────────────
    for item in priorities:
        session.add(UserPriority(
            user_id=user.id,
            priority=item["priority"],
            rank=item["rank"],  # FIX: was item["rank] (missing closing quote)
        ))

    # ── Step 9: activate user + mark invite accepted + audit ───────────────────
    # All required data is now staged. Activate atomically.
    user.status = UserStatus.ACTIVE

    invite.status = InviteStatus.ACCEPTED
    invite.accepted_at = now
    invite.accepted_by_id = user.id

    _log_audit(
        session,
        user=user,
        activity_type=ActivityType.JOINED_ORGANIZATION,
        reference_id=invite.organization_id,
        reference_type=ReferenceType.ORGANIZATION,
        metadata={"invited_by": str(invite.invited_by), "provider": OAuthProvider.PASSWORD},
    )
    await session.flush()

    # ── Step 10: issue tokens ──────────────────────────────────────────────────
    return await issue_tokens(session, user, device_name, device_fingerprint, ip_address)