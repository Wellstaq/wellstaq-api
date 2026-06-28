"""
app/services/auth/signup_service.py

Org owner self-registration (3-step: OTP → verify → signup).

Owner registration flow:
  - User created with status=ACTIVE immediately (no onboarding required)
  - Tokens issued immediately after registration
  - Slug returned alongside tokens so frontend can display the org URL
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.core.constants import (
    ActivityType,
    OAuthProvider,
    ReferenceType,
    RoleName,
    UserStatus,
)
from app.models import Organization, User, UserAuthProvider
from .organization_service import (
    assign_role,
    check_business_name_available,
    check_phone_available,
    get_system_role,
    seed_default_permissions,
    unique_slug,
)
from .token_service import issue_tokens
from ._helpers import (
    AuthError,
    _create_user_settings,
    _decode_email_verification_token,
    _log_audit,
)


async def org_owner_signup(
    session: AsyncSession,
    email_verification_token: str,
    first_name: str,
    last_name: str,
    password: str,
    business_name: str,
    phone_number: str,
    employee_count: int,
    organization_type: str,
    work_model: str,
    device_name: str | None = None,
    device_fingerprint: str | None = None,
    ip_address: str | None = None,
) -> dict:
    """
    Create Organization + owner User atomically.

    Owner is created ACTIVE immediately — no onboarding/baseline required.
    Tokens issued immediately.
    Response includes 'slug' so the frontend can display the org URL.

    Transaction order (resolves circular owner_id FK):
      1. Decode + validate email_verification_token
      2. Availability pre-checks (email, business_name, phone)
      3. Create Org (owner_id=None) → flush → org.id
      4. Create User (ACTIVE) → flush → user.id
      5. Patch org.owner_id = user.id
      6. AuthProvider + Settings + role + permissions (one flush)
      7. Audit log + final flush
      8. Issue tokens, append slug to response
    """
    # ── Step 1 ─────────────────────────────────────────────────────────────────
    email = _decode_email_verification_token(email_verification_token)

    # ── Step 2 ─────────────────────────────────────────────────────────────────
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise AuthError("An account with this email already exists.", status.HTTP_409_CONFLICT)

    if not await check_business_name_available(session, business_name):
        raise AuthError(
            "An organization with this business name already exists.",
            status.HTTP_409_CONFLICT,
        )
    if not await check_phone_available(session, phone_number):
        raise AuthError(
            "This phone number is already associated with another organization.",
            status.HTTP_409_CONFLICT,
        )

    # ── Step 3 ─────────────────────────────────────────────────────────────────
    slug = await unique_slug(session, business_name)
    org = Organization(
        business_name=business_name.strip(),
        slug=slug,
        employee_count=employee_count,
        organization_type=organization_type,
        work_model=work_model,
        owner_id=None,
    )
    session.add(org)
    await session.flush()  # need org.id before creating user

    # ── Step 4 ─────────────────────────────────────────────────────────────────
    user = User(
        first_name=first_name,
        last_name=last_name,
        phone_number=phone_number.strip(),
        email=email,
        hashed_password=hash_password(password),
        organization_id=org.id,
        status=UserStatus.ACTIVE,  # owners are active immediately, no onboarding
    )
    session.add(user)
    await session.flush()  # need user.id before patching org.owner_id

    # ── Step 5 ─────────────────────────────────────────────────────────────────
    org.owner_id = user.id

    # ── Step 6: ancillary records ──────────────────────────────────────────────
    session.add(UserAuthProvider(
        user_id=user.id,
        organization_id=org.id,
        provider=OAuthProvider.PASSWORD,
        provider_user_id=None,
        access_token=None,
        refresh_token=None,
    ))
    session.add(_create_user_settings(user))

    super_admin_role = await get_system_role(session, RoleName.SUPER_ADMIN)
    if super_admin_role:
        assign_role(user, super_admin_role)
        await session.flush()  # role must land before seeding permissions
        await seed_default_permissions(session, user, super_admin_role.name)

    # ── Step 7 ─────────────────────────────────────────────────────────────────
    _log_audit(
        session,
        user=user,
        activity_type=ActivityType.ACCOUNT_CREATED,
        reference_id=org.id,
        reference_type=ReferenceType.ORGANIZATION,
        metadata={"role": RoleName.SUPER_ADMIN, "slug": slug},
    )
    await session.flush()

    # ── Step 8 ─────────────────────────────────────────────────────────────────
    tokens = await issue_tokens(session, user, device_name, device_fingerprint, ip_address)
    tokens["slug"] = slug  # FIX: restored — was missing in previous version
    return tokens