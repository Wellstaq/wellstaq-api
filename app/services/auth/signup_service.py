"""
app/services/auth/signup_service.py

Org owner self-registration (3-step: OTP → verify → signup).

Owner registration flow:
  - User created with status=ACTIVE immediately (no onboarding required)
  - Tokens issued immediately after registration
  - Slug returned alongside tokens so frontend can display the org URL

Branch/role redesign notes:
  - Every User now requires a branch_id (home branch, NOT NULL). Since
    the owner is the very first user in a brand-new org, a Headquarters
    branch is created in the same transaction and the owner is assigned
    to it. is_headquarters=True marks it as protected — it cannot be
    renamed or deleted (see delete_branch.py / update_branch.py).
  - super_admin is intentionally NOT tracked via UserRoleAssignment.
    One-per-org is enforced structurally by Organization.owner_id
    (there is exactly one). The owner simply gets role_id=super_admin
    and branch_id=<HQ branch> — no assignment row needed.
"""

from __future__ import annotations

import logging

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.exc import IntegrityError

from app.auth.security import hash_password
from app.core.constants import (
    ActivityType,
    OAuthProvider,
    ReferenceType,
    RoleName,
    UserStatus,
)
from app.models import Branch, Organization, Role, User, UserAuthProvider
from scripts.seed_permissions import seed_permissions as _seed_permissions_catalogue
from scripts.seed_permissions import seed_roles as _seed_roles_catalogue
from .organization_service import (
    assign_role,
    check_business_name_available,
    check_phone_available,
    get_system_role,
    seed_super_admin_permissions,
    unique_slug,
)
from .token_service import issue_tokens
from ._helpers import (
    AuthError,
    _create_user_settings,
    _decode_email_verification_token,
    _log_audit,
    _validate_password_strength,
)
from app.services.payment import start_trial
from app.core.constants import HEADQUARTERS_BRANCH_NAME

logger = logging.getLogger(__name__)

# The display name for the auto-created org-wide home branch.
# create_branch.py blocks any regular branch from claiming this name
# so it always uniquely identifies the protected HQ branch.


async def _get_or_self_heal_super_admin_role(session: AsyncSession) -> Role:
    """
    Look up the super_admin system role. If it's missing — almost always
    a sign the seed script hasn't run yet on a fresh deploy — attempt a
    one-time idempotent re-seed of both roles and permissions, then
    retry, rather than either:
      (a) silently creating an owner with no role/permissions, or
      (b) permanently rejecting every signup until someone notices.

    seed_roles()/seed_permissions() only insert rows that don't already
    exist, so calling them here is always safe — not a destructive op.

    Permissions are re-seeded alongside roles because
    seed_super_admin_permissions() grants every permission currently in
    the permissions table. If only the role were missing, that's usually
    because both are missing together (fresh DB).

    Raises RuntimeError if the role is still missing after re-seeding —
    that means RoleName.SUPER_ADMIN no longer matches the seed catalogue,
    which needs a human to look at.
    """
    role = await get_system_role(session, RoleName.SUPER_ADMIN)
    if role is not None:
        return role

    logger.warning(
        "System role 'super_admin' not found during signup — "
        "attempting to self-heal by re-running the platform seed."
    )
    try:
        async with session.begin_nested():
            await _seed_roles_catalogue(session)
            await _seed_permissions_catalogue(session)
    except IntegrityError:
        logger.info(
            "Seed insert hit a UNIQUE conflict — likely a concurrent "
            "signup won the self-heal race. Re-checking for the role."
        )

    role = await get_system_role(session, RoleName.SUPER_ADMIN)
    if role is None:
        raise RuntimeError(
            "System role 'super_admin' still missing after re-seeding. "
            "RoleName.SUPER_ADMIN likely no longer matches the seed "
            "catalogue in scripts/seed_permissions.py — needs manual "
            "investigation, not another automatic retry."
        )
    return role


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
    Create Organization + Headquarters Branch + owner User atomically.

    Owner is created ACTIVE immediately — no onboarding/baseline required.
    Tokens issued immediately.
    Response includes 'slug' so the frontend can display the org URL.

    Transaction order:
      1. Decode + validate email_verification_token
      2. Availability pre-checks (email, business_name, phone)
      3. Create Org (owner_id=None) → flush → org.id
      4. Create Headquarters Branch (is_headquarters=True) → flush → branch.id
      5. Create User (ACTIVE, branch_id=HQ) → flush → user.id
      6. Patch org.owner_id = user.id, hq_branch.created_by = user.id
      7. AuthProvider + Settings + role + permissions → flush
      8. Audit log → flush
      9. Issue tokens, append slug to response
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
    
      # ── Step 2a: password strength ────────────────────────────────────────────
    _validate_password_strength(password)

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
    await session.flush()

    # ── Step 4: Headquarters branch ─────────────────────────────────────────────
    # is_headquarters=True marks this branch as protected. The service layer
    # uses this flag to block deletion (delete_branch.py) and renaming
    # (update_branch.py). created_by is backfilled in step 6 once user.id
    # is available — it cannot be set here because the user doesn't exist yet.
    hq_branch = Branch(
        organization_id=org.id,
        name=HEADQUARTERS_BRANCH_NAME,
        is_headquarters=True,
        created_by=None,
    )
    session.add(hq_branch)
    await session.flush()
    
    # ── Step 5 ─────────────────────────────────────────────────────────────────
    user = User(
        first_name=first_name,
        last_name=last_name,
        phone_number=phone_number.strip(),
        email=email,
        hashed_password=hash_password(password),
        organization_id=org.id,
        branch_id=hq_branch.id,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()

    # ── Step 6 ─────────────────────────────────────────────────────────────────
    org.owner_id = user.id
    hq_branch.created_by = user.id

    # ── Step 7: ancillary records ───────────────────────────────────────────────
    session.add(UserAuthProvider(
        user_id=user.id,
        organization_id=org.id,
        provider=OAuthProvider.PASSWORD,
        provider_user_id=None,
        access_token=None,
        refresh_token=None,
    ))
    session.add(_create_user_settings(user))

    super_admin_role = await _get_or_self_heal_super_admin_role(session)
    assign_role(user, super_admin_role)
    await session.flush()
    await seed_super_admin_permissions(session, user)

    # ── Step 7b: start free trial ─────────────────────────────────────────────
    await start_trial(session=session, organization_id=org.id)

    # ── Step 8 ─────────────────────────────────────────────────────────────────
    await _log_audit(
        session,
        user=user,
        activity_type=ActivityType.ACCOUNT_CREATED,
        reference_id=org.id,
        reference_type=ReferenceType.ORGANIZATION,
        metadata={"role": RoleName.SUPER_ADMIN, "slug": slug, "hq_branch_id": hq_branch.id},
    )
    await session.flush()

    # ── Step 9 ─────────────────────────────────────────────────────────────────
    tokens = await issue_tokens(session, user, device_name, device_fingerprint, ip_address)
    tokens["slug"] = slug
    return tokens