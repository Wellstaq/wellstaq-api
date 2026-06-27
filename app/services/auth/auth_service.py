"""
app/services/auth_service.py

Authentication business logic. Routes call these functions and commit.
Services never commit — the router owns the transaction boundary.

Flows
─────
1. org_owner_signup       — create org + owner atomically (post OTP verify)
2. request_invite_otp     — validate invite, send OTP to invite's email
3. verify_invite_otp      — verify OTP, return invite_verification_token
4. invite_register        — register via OrganizationInvite
5. password_login         — email + password, 2FA-aware
6. oauth_login            — Google / Apple frontend-first
7. refresh_access_token   — rotate refresh token, return new pair
8. logout                 — revoke refresh token for this device

2FA flows    → app/services/two_factor_service.py
Token/device → app/services/token_service.py
Org/slug     → app/services/organization_service.py
Constants    → app/core/constants.py

OAuth verification
──────────────────
The router pre-verifies Google/Apple tokens before calling oauth_login().
oauth_login() accepts pre-verified claims directly — it does NOT re-verify.
This eliminates the double round-trip to Google/Apple JWKS on every OAuth login.
The router is the single verification point; the service trusts those claims.

Token decoding
──────────────
All token decode + validate + extract sequences are centralised in
_decode_email_verification_token() and _decode_invite_verification_token()
to avoid copy-paste divergence across flows.

Race conditions
────────────────
Availability pre-checks (email, business_name, phone) give user-friendly
early errors. DB UNIQUE constraints are the real safety net. Routers
should also catch IntegrityError and map to 409 Conflict.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import TokenError, decode_token
from app.auth.security import hash_password, verify_password
from app.core.constants import (
    ActivityType,
    InviteStatus,
    OAuthProvider,
    ReferenceType,
    RoleName,
    TokenType,
    UserStatus,
)
from app.models import (
    DailyCheckin,
    Organization,
    OrganizationInvite,
    User,
    UserActivityFeed,
    UserAuthProvider,
    UserPermission,
    UserPriority,
    UserSettings,
)
from .errors import AppError
from .organization_service import (
    assign_role,
    check_business_name_available,
    check_phone_available,
    get_system_role,
    seed_default_permissions,
    unique_slug,
)
from .token_service import (
    hash_refresh_token,
    issue_tokens,
    revoke_device,
    rotate_refresh_token,
)
from .two_factor_service import issue_two_fa_challenge

logger = logging.getLogger(__name__)


# ── Error ──────────────────────────────────────────────────────────────────────

class AuthError(AppError):
    pass


# ── Token decode helpers ───────────────────────────────────────────────────────
# Centralised here so the decode → type-check → extract sequence is
# never copy-pasted. If the token shape changes, fix it in one place.

def _decode_email_verification_token(raw_token: str) -> str:
    """
    Decode an email_verification_token and return the verified email.
    Raises AuthError on any problem (expired, wrong type, missing claim).
    """
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
    """
    Decode an invite_verification_token and return (email, invite_code).
    Raises AuthError on any problem.
    """
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


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _mask_email(email: str) -> str:
    """'john.doe@example.com' → 'j***@example.com'"""
    local, domain = email.rsplit("@", 1)
    return local[0] + "***@" + domain


def _create_user_settings(user: User) -> UserSettings:
    return UserSettings(
        user_id=user.id,
        organization_id=user.organization_id,
    )


def _make_invite_verification_token(email: str, invite_code: str) -> str:
    """
    Build a 30-minute invite_verification_token.

    Uses jwt.py's private _create_token() because the public
    create_access_token() doesn't support custom expiry or extra claims.
    If you add a public create_custom_token() to jwt.py, use that instead.
    """
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


async def _load_pending_invite(
    session: AsyncSession,
    invite_code: str,
    now: datetime,
    *,
    load_permissions: bool = False,
) -> OrganizationInvite:
    """
    Load and validate a pending, unexpired invite.
    Pass load_permissions=True to eager-load invite_permissions in one query.
    """
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


def _log_audit(
    session,
    *,
    user: User,
    activity_type: ActivityType,
    reference_id: str | None = None,
    reference_type: ReferenceType | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Append an immutable audit event to the activity feed.
    Call before flush — the row is written atomically with the triggering action.
    """
    session.add(UserActivityFeed(
        user_id=user.id,
        organization_id=user.organization_id,
        activity_type=activity_type,
        reference_id=reference_id or user.id,
        reference_type=reference_type or ReferenceType.USER,
        activity_metadata=metadata or {},
    ))


# ── 1. Org owner signup ────────────────────────────────────────────────────────

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
    Create a new Organization + owner User atomically.

    Email decoded from email_verification_token — never re-supplied by
    the client, preventing substitution attacks.

    Pre-checks are UX guards only. DB UNIQUE constraints are the real
    safety net — routers must also catch IntegrityError → 409.

    Transaction order (resolves circular owner_id FK):
      1. Decode + validate email_verification_token
      2. Availability pre-checks (email, business_name, phone)
      3. Create Org (owner_id=None) → flush → org.id
      4. Create User → flush → user.id
      5. Patch org.owner_id = user.id
      6. AuthProvider + Settings + role + permissions (one flush)
      7. Audit log entry
      8. issue_tokens()
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
        phone_number=phone_number.strip(),
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
        email=email,
        hashed_password=hash_password(password),
        organization_id=org.id,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()  # need user.id before patching org.owner_id

    # ── Step 5 ─────────────────────────────────────────────────────────────────
    org.owner_id = user.id

    # ── Step 6: ancillary records — one flush covers all ──────────────────────
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
        await session.flush()  # role assignment must land before seeding permissions
        await seed_default_permissions(session, user, super_admin_role.name)

    # ── Step 7: audit ──────────────────────────────────────────────────────────
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
    tokens["slug"] = slug
    return tokens


# ── 2. Request invite OTP ──────────────────────────────────────────────────────

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


# ── 3. Verify invite OTP ───────────────────────────────────────────────────────

async def verify_invite_otp(
    session: AsyncSession, invite_code: str, code: str
) -> str:
    """
    Verify the OTP for an invite. Returns an invite_verification_token.

    OtpError propagates to the router (which must commit() to persist
    attempt_count before re-raising).
    """
    from .otp_service import verify_otp

    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(session, invite_code, now)
    await verify_otp(session, invite.invited_email, code)
    return _make_invite_verification_token(invite.invited_email, invite_code)


# ── 4. Invite register ─────────────────────────────────────────────────────────

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
    Register via an OrganizationInvite.

    Email and invite_code decoded from invite_verification_token —
    client cannot supply either (prevents invite-squatting and
    token substitution attacks).
    """
    # ── Step 1: decode token ───────────────────────────────────────────────────
    email, invite_code = _decode_invite_verification_token(invite_verification_token)

    # ── Step 2: re-validate invite (eager-load permissions in same query) ──────
    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(
        session, invite_code, now, load_permissions=True
    )

    # ── Step 3: token substitution guard ──────────────────────────────────────
    if invite.invited_email.lower() != email:
        raise AuthError("Invalid or expired invite verification token.")

    # ── Step 4: duplicate guard ────────────────────────────────────────────────
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise AuthError("An account already exists for this email.", status.HTTP_409_CONFLICT)

    # ── Step 5: create user ────────────────────────────────────────────────────
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
        status=UserStatus.ACTIVE,
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

    for ip in invite.invite_permissions:
        session.add(UserPermission(
            user_id=user.id,
            permission_id=ip.permission_id,
            organization_id=ip.organization_id,
            branch_id=ip.branch_id,
            granted_by=invite.invited_by,
        ))

    session.add(DailyCheckin(
        user_id=user.id,
        mood=baseline["mood"],
        mood_reason=baseline["mood_reason"],
        energy_level=baseline["energy_level"],
        energy_level_reason=baseline["energy_level_reason"],
        stress_level=baseline["stress_level"],
        stress_level_reason=baseline["stress_level_reason"],
        work_life_balance=baseline["work_life_balance"],
        work_life_balance_reason=baseline["work_life_balance_reason"],
    ))

    for item in priorities:
        session.add(UserPriority(
            user_id=user.id,
            priority=item["priority"],
            rank=item["rank"],
        ))

    # ── Step 7: mark invite accepted + audit ───────────────────────────────────
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

    # ── Step 8: issue tokens ───────────────────────────────────────────────────
    return await issue_tokens(session, user, device_name, device_fingerprint, ip_address)


# ── 5. Password login ──────────────────────────────────────────────────────────

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
    avoid the previous two-query pattern (user → then provider check).
    """
    result = await session.execute(
        select(User)
        .options(
            selectinload(User.settings),
            selectinload(User.auth_providers),  # eliminates the second provider query
        )
        .where(User.email == email.lower())
    )
    user = result.scalar_one_or_none()

    dummy_hash = "$2b$12$notarealhashjustpaddingtomakethisalongstring00000000000"
    stored_hash = user.hashed_password if user else dummy_hash
    password_ok = verify_password(password, stored_hash)

    if not password_ok or user is None:
        # Audit failed login attempt when user exists — needed for lockout policies
        # and security alerting. Don't log when user is None (avoids confirming the
        # email doesn't exist — enumeration defence).
        if user is not None:
            _log_audit(
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

    # Check provider from the already-loaded relationship — no second query
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
            user=user,
            method=settings_row.two_fa_method,
            device_name=device_name,
            device_fingerprint=device_fingerprint,
            ip_address=ip_address,
        )

    _log_audit(
        session,
        user=user,
        activity_type=ActivityType.LOGIN,
        metadata={"provider": OAuthProvider.PASSWORD},
    )
    await session.flush()

    return await issue_tokens(session, user, device_name, device_fingerprint, ip_address)


# ── 6. OAuth login ─────────────────────────────────────────────────────────────

async def oauth_login(
    session: AsyncSession,
    provider: str,
    claims: dict[str, Any],         # pre-verified by the router — NOT re-verified here
    invite_code: str | None = None,
    first_name: str | None = None,  # Apple only — not in the ID token after first login
    last_name: str | None = None,
    device_name: str | None = None,
    device_fingerprint: str | None = None,
    ip_address: str | None = None,
) -> dict:
    """
    Frontend-first OAuth. Existing user → login. New user → require invite_code.

    Claims are pre-verified by the router (_verify_google / _verify_apple)
    and passed in directly. This function does NOT call any OAuth provider
    API — it trusts the claims dict the router supplies.

    This eliminates the previous double-verification pattern where both the
    router and the service independently verified the same token.
    """
    provider = provider.lower()

    if provider == OAuthProvider.GOOGLE:
        provider_user_id = claims.get("sub")
        email = claims.get("email", "").lower()
        first_name = claims.get("given_name") or claims.get("name", "").split(" ", 1)[0]
        last_name = claims.get("family_name") or (
            claims.get("name", "").split(" ", 1)[1]
            if " " in claims.get("name", "") else ""
        )
        avatar_url: str | None = claims.get("picture")

    elif provider == OAuthProvider.APPLE:
        provider_user_id = claims.get("sub")
        email = claims.get("email", "").lower()
        avatar_url = None
        # first_name / last_name come from the payload, not the token

    else:
        raise AuthError(f"Unsupported OAuth provider '{provider}'.", status.HTTP_400_BAD_REQUEST)

    if not provider_user_id:
        raise AuthError(
            "Could not extract user identity from provider token.",
            status.HTTP_401_UNAUTHORIZED,
        )

    # ── Existing user — single join query ─────────────────────────────────────
    result = await session.execute(
        select(User)
        .join(UserAuthProvider, UserAuthProvider.user_id == User.id)
        .where(
            UserAuthProvider.provider == provider,
            UserAuthProvider.provider_user_id == provider_user_id,
        )
    )
    user = result.scalar_one_or_none()

    if user:
        if user.status in (UserStatus.SUSPENDED, UserStatus.INACTIVE):
            raise AuthError(
                "Your account is not active. Contact your administrator.",
                status.HTTP_403_FORBIDDEN,
            )
        if avatar_url:
            user.avatar_url = avatar_url

        _log_audit(
            session,
            user=user,
            activity_type=ActivityType.LOGIN,
            metadata={"provider": provider},
        )
        await session.flush()
        return await issue_tokens(session, user, device_name, device_fingerprint, ip_address)

    # ── New user — requires invite ─────────────────────────────────────────────
    if not invite_code:
        raise AuthError(
            "An invite code is required to create a new account with social login.",
            status.HTTP_400_BAD_REQUEST,
        )

    now = datetime.now(timezone.utc)
    invite = await _load_pending_invite(session, invite_code, now)

    final_email = invite.invited_email.lower()
    existing = await session.execute(select(User).where(User.email == final_email))
    if existing.scalar_one_or_none():
        raise AuthError("An account already exists for this email.", status.HTTP_409_CONFLICT)

    fallback = final_email.split("@")[0]
    user = User(
        first_name=first_name or fallback,
        last_name=last_name or "",
        email=final_email,
        hashed_password=None,
        avatar_url=avatar_url,
        organization_id=invite.organization_id,
        branch_id=invite.branch_id,
        role_id=invite.invited_role_id,
        department_id=invite.invited_department_id,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()  # need user.id

    session.add(UserAuthProvider(
        user_id=user.id,
        organization_id=user.organization_id,
        provider=provider,
        provider_user_id=provider_user_id,
        access_token=None,
        refresh_token=None,
    ))
    session.add(_create_user_settings(user))

    if not invite.invited_role_id:
        employee_role = await get_system_role(session, RoleName.EMPLOYEE)
        if employee_role:
            assign_role(user, employee_role)
    await session.flush()
    await seed_default_permissions(session, user, RoleName.EMPLOYEE)

    invite.status = InviteStatus.ACCEPTED
    invite.accepted_at = now
    invite.accepted_by_id = user.id

    _log_audit(
        session,
        user=user,
        activity_type=ActivityType.JOINED_ORGANIZATION,
        reference_id=invite.organization_id,
        reference_type=ReferenceType.ORGANIZATION,
        metadata={"invited_by": str(invite.invited_by), "provider": provider},
    )
    await session.flush()

    return await issue_tokens(session, user, device_name, device_fingerprint, ip_address)


# ── 7. Refresh access token ────────────────────────────────────────────────────

async def refresh_access_token(
    session: AsyncSession, raw_refresh_token: str
) -> dict:
    """
    Rotate the refresh token and return a new access + refresh pair.

    Rotation: each refresh token is single-use. A leaked token can only
    be used once; the next legitimate request finds its hash replaced
    and must re-login.
    """
    from app.models import UserDevice

    try:
        payload = decode_token(raw_refresh_token)
    except TokenError as exc:
        raise AuthError(
            "Refresh token is invalid or expired. Please log in again.",
            status.HTTP_401_UNAUTHORIZED,
        ) from exc

    if payload.get("type") != TokenType.REFRESH:
        raise AuthError("Not a refresh token.", status.HTTP_401_UNAUTHORIZED)

    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Malformed token.", status.HTTP_401_UNAUTHORIZED)

    token_hash = hash_refresh_token(raw_refresh_token)
    result = await session.execute(
        select(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.refresh_token_hash == token_hash,
        )
    )
    device = result.scalar_one_or_none()

    if not device:
        raise AuthError(
            "Refresh token not recognised. Please log in again.",
            status.HTTP_401_UNAUTHORIZED,
        )
    if device.revoked:
        raise AuthError(
            "This session has been logged out. Please log in again.",
            status.HTTP_401_UNAUTHORIZED,
        )

    user_result = await session.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user or user.status in (UserStatus.SUSPENDED, UserStatus.INACTIVE):
        raise AuthError(
            "Account not available. Please log in again.", status.HTTP_401_UNAUTHORIZED
        )

    return await rotate_refresh_token(session, user, device)


# ── 8. Logout ──────────────────────────────────────────────────────────────────

async def logout(session: AsyncSession, raw_refresh_token: str) -> None:
    """
    Revoke the refresh token for this device.
    Silently succeeds if the token is already expired or unrecognised —
    idempotent so double-logout from a flaky client doesn't 4xx.
    """
    from app.models import UserDevice

    try:
        payload = decode_token(raw_refresh_token)
    except TokenError:
        return

    user_id = payload.get("sub")
    if not user_id:
        return

    token_hash = hash_refresh_token(raw_refresh_token)
    result = await session.execute(
        select(UserDevice).where(
            UserDevice.user_id == user_id,
            UserDevice.refresh_token_hash == token_hash,
        )
    )
    device = result.scalar_one_or_none()
    if device and not device.revoked:
        await revoke_device(session, device)


# ── Get current user ───────────────────────────────────────────────────────────

async def get_me(session: AsyncSession, user_id: str) -> User:
    result = await session.execute(
        select(User)
        .options(selectinload(User.settings))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise AuthError("User not found.", status.HTTP_404_NOT_FOUND)
    return user