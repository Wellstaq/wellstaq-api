"""
app/services/auth/invite_service.py

Invite-based registration flows:
  1. request_invite_otp  — validate invite, send OTP
  2. verify_invite_otp   — verify OTP, return invite_verification_token
  3. invite_register     — complete registration with wellbeing baseline

Employee registration flow:
  - User created with status=PENDING
  - Wellbeing baseline + priorities recorded
  - DepartmentMember row created to assign user to their department
  - User activated (ACTIVE) atomically before tokens issued

Key design note — department membership:
  User has NO department_id column. Department membership is tracked
  via the DepartmentMember join table (one row per user, enforced by
  UniqueConstraint("user_id") on DepartmentMember). The invited_department_id
  on the invite tells us which department to create that row for.
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
    UserPriority,
    UserWellbeingBaseline,
)
from app.models.department import DepartmentMember
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
    """
    from .otp_service import verify_otp

    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(session, invite_code, now)
    await verify_otp(session, invite.invited_email, code)
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
      1.  Decode token → email, invite_code
      2.  Re-validate invite
      3.  Token substitution guard
      4.  Duplicate email guard
      5.  Create User (PENDING — not yet active)
      6.  Ancillary records: auth provider, settings
      7.  DepartmentMember row — assigns user to invited_department_id
      8.  Wellbeing baseline rows
      9.  Priority rows
      10. Activate user (ACTIVE) + mark invite accepted + audit
      11. Issue tokens

    Department membership note:
      User has no department_id column — membership lives in the
      DepartmentMember join table. Step 7 creates that row using
      invite.invited_department_id. UniqueConstraint("user_id") on
      DepartmentMember ensures the user can only ever be in one
      department at a time.
    """
    # ── Step 1 ─────────────────────────────────────────────────────────────────
    email, invite_code = _decode_invite_verification_token(invite_verification_token)

    # Normalize — belt-and-suspenders, token already stores lowercased email
    email = email.lower().strip()

    # ── Step 2 ─────────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(session, invite_code, now)

    # ── Step 3: token substitution guard ──────────────────────────────────────
    # Ensures the token was issued for exactly this invite, not swapped
    if invite.invited_email.lower().strip() != email:
        raise AuthError("Invalid or expired invite verification token.")

    # ── Step 4: duplicate email guard ─────────────────────────────────────────
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise AuthError(
            "An account already exists for this email.",
            status.HTTP_409_CONFLICT,
        )

    # ── Step 5: create user as PENDING ────────────────────────────────────────
    # Department is NOT set here — User has no department_id column.
    # Department membership is created in Step 7 via DepartmentMember.
    # first_name and last_name are stripped of whitespace before storing.
    user = User(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=email,
        hashed_password=hash_password(password),
        organization_id=invite.organization_id,
        branch_id=invite.branch_id,        # nullable — may be None for org-level invites
        role_id=invite.invited_role_id,
        country=country.strip() if country else None,
        state=state.strip() if state else None,
        status=UserStatus.PENDING,          # activated atomically in step 10
    )
    session.add(user)
    await session.flush()  # need user.id for all child records below

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

    # ── Step 7: department membership ─────────────────────────────────────────
    # Creates the DepartmentMember join row that places this user into
    # the department the inviter pre-selected on the invite.
    # UniqueConstraint("user_id") on DepartmentMember guarantees the user
    # cannot be in two departments simultaneously — this is the first and
    # only DepartmentMember row for this user_id.
    session.add(DepartmentMember(
        department_id=invite.invited_department_id,
        user_id=user.id,
    ))

    # ── Step 8: wellbeing baseline ────────────────────────────────────────────
    if "entries" not in baseline:
        raise AuthError("Invalid baseline format.", status.HTTP_400_BAD_REQUEST)

    recorded = datetime.now(timezone.utc)
    for entry in baseline.get("entries", []):
        session.add(UserWellbeingBaseline(
            user_id=user.id,
            organization_id=user.organization_id,
            dimension=entry["dimension"],
            level=entry["level"],
            reason=entry.get("reason"),
            recorded_at=recorded,
        ))

    # ── Step 9: priorities ────────────────────────────────────────────────────
    for item in priorities:
        session.add(UserPriority(
            user_id=user.id,
            priority=item["priority"],
            rank=item["rank"],
        ))

    # ── Step 10: activate + mark invite accepted + audit ──────────────────────
    # All required data is now staged in the session.
    # Flip status atomically — if anything above failed, we never reach here.
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
        metadata={
            "invited_by": str(invite.invited_by),
            "provider": OAuthProvider.PASSWORD,
            "department_id": invite.invited_department_id,
            "branch_id": str(invite.branch_id) if invite.branch_id else None,
        },
    )
    await session.flush()

    # ── Step 11: issue tokens ─────────────────────────────────────────────────
    # Eagerly reload the role relationship before calling issue_tokens.
    # token_service.build_claims() accesses user.role which is a lazy
    # relationship — in async SQLAlchemy, implicit lazy loads raise
    # MissingGreenlet. Refreshing with selectinload here resolves it
    # without an extra round-trip (refresh only fetches what's listed).
    from sqlalchemy.orm import selectinload
    await session.refresh(user, ["role"])

    return await issue_tokens(session, user, device_name, device_fingerprint, ip_address)