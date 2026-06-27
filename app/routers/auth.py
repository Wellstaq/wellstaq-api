"""
app/routers/auth.py

Authentication routes — thin HTTP wrappers around auth_service.py and otp_service.py.

Endpoints
─────────
Signup (3-step):
  POST /auth/signup/otp/request  — Step 1: send email OTP (handled by otp_service)
  POST /auth/signup/otp/verify   — Step 2: verify OTP → email_verification_token
  POST /auth/signup              — Step 3: create org + owner

Invite:
  POST /auth/register/invite     — accept invite + create account

Login:
  POST /auth/login               — email + password (JSON body)
  POST /auth/login/google        — Google frontend-first OAuth
  POST /auth/login/apple         — Apple frontend-first OAuth

2FA (after login returns TwoFaChallengeResponse):
  POST /auth/2fa/verify          — submit code + challenge token → full JWT

2FA management (authenticated):
  POST /auth/2fa/setup           — initiate 2FA enrollment
  POST /auth/2fa/disable         — turn off 2FA

Session:
  POST /auth/refresh             — exchange refresh token for new access token
  POST /auth/logout              — revoke refresh token (this device)
  GET  /auth/me                  — identity + permissions + 2FA status

This file handles ONLY:
  • HTTP shape (Pydantic schemas in/out)
  • AuthError / OtpError → HTTP status codes
  • Device fingerprint extraction from headers
  • Pre-flight token verification for Google/Apple before hitting the DB

All business logic lives in auth_service.py and otp_service.py.
"""

import asyncio
import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.schemas.auth import (
    AccessTokenResponse,
    InviteRegisterRequest,
    LoginRequest,
    OAuthRequest,
    OrgOwnerRegisterRequest,
    OtpRequestSchema,
    OtpVerifySchema,
    InviteOtpRequestSchema,
    InviteOtpVerifySchema,
    RefreshRequest,
    TokenResponse,
    TwoFaChallengeResponse,
    TwoFaDisableRequest,
    TwoFaSetupRequest,
    TwoFaSetupResponse,
    TwoFaVerifyRequest,
    UserMeResponse,
)
from app.services.auth import (
    AuthError,
    OtpError,
    check_business_name_available,
    check_phone_available,
    disable_two_fa,
    get_me,
    initiate_two_fa_setup,
    invite_register,
    logout,
    oauth_login,
    org_owner_signup,
    password_login,
    refresh_access_token,
    request_invite_otp,
    request_otp,
    verify_invite_otp,
    verify_otp,
    verify_two_fa_challenge,
)
from app.core.guards import request_guard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Typed device metadata ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DeviceMeta:
    """
    Immutable snapshot of per-request device context.

    Typed rather than dict so callers get attribute access, IDE completion,
    and a clear contract instead of stringly-typed key lookups.

    ip_address is audit-only — intentionally excluded from device_fingerprint
    so mobile users on cellular don't lose their session when carrier NAT
    reassigns their IP mid-session.
    """
    device_name: str | None
    device_fingerprint: str | None
    ip_address: str | None


def _device_fingerprint(request: Request) -> str | None:
    """
    Stable session identifier derived from client-supplied headers.

    Uses the full SHA-256 hex digest (64 chars) — no truncation.
    The truncated 16-char version (:16) was 64 bits of entropy, which is
    enough in practice but there is no reason to throw away free bits when
    storing a string in a VARCHAR column.

    x-device-id (set by the client SDK) is the preferred stable key.
    user-agent alone is a weak fallback for clients that don't send x-device-id.
    IP is deliberately excluded — see DeviceMeta docstring.
    """
    user_agent = request.headers.get("user-agent", "")
    x_device_id = request.headers.get("x-device-id", "")
    if not user_agent and not x_device_id:
        return None
    raw = f"{user_agent}|{x_device_id}"
    return hashlib.sha256(raw.encode()).hexdigest()  # full 64-char digest


def _device_meta(request: Request) -> DeviceMeta:
    return DeviceMeta(
        device_name=request.headers.get("x-device-name"),
        device_fingerprint=_device_fingerprint(request),
        ip_address=request.client.host if request.client else None,
    )




# ── Google token verification ──────────────────────────────────────────────────

async def _verify_google(id_token: str) -> dict:
    """
    Verify a Google ID token against Google's cached public keys.

    google.auth uses blocking network I/O and CPU-bound RSA verification.
    Both run in the default thread-pool executor so they don't stall the
    event loop under concurrent requests.

    The service layer re-verifies internally — defence in depth.
    Keeping verification here means the DB is never touched for an invalid
    token, even if the service is called from a non-HTTP context later.
    """
    def _sync_verify() -> dict:
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token as g_id_token
        return g_id_token.verify_oauth2_token(
            id_token,
            g_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_verify)
    except Exception as exc:
        logger.warning(
            "Google ID token verification failed",
            extra={"error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Google token: {exc}",
        )


# ── Apple token verification with JWKS caching ────────────────────────────────

# Module-level JWKS cache.
# Apple's signing keys rotate infrequently; 1-hour TTL is safe and eliminates
# a live HTTP round-trip to Apple on every login request.
# Replace with Redis when available to share the cache across workers.
_apple_jwks_cache: dict = {}
_APPLE_JWKS_TTL = 3600  # seconds


async def _get_apple_jwks() -> list[dict]:
    """
    Fetch Apple's public JWKS, returning the cached copy if it is fresh.
    Busts the cache and re-fetches when the TTL expires.
    """
    now = time.monotonic()
    cached = _apple_jwks_cache.get("keys")
    fetched_at = _apple_jwks_cache.get("fetched_at", 0.0)

    if cached and (now - fetched_at) < _APPLE_JWKS_TTL:
        return cached

    import httpx
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get("https://appleid.apple.com/auth/keys")
        resp.raise_for_status()
        keys = resp.json()["keys"]

    _apple_jwks_cache["keys"] = keys
    _apple_jwks_cache["fetched_at"] = now
    return keys


async def _verify_apple(id_token: str) -> dict:
    """
    Verify an Apple ID token against Apple's public JWKS.

    JWKS is cached at module level (_APPLE_JWKS_TTL = 1 h).
    JWT decode (RSA) is CPU-bound and runs in a thread-pool executor
    to avoid blocking the event loop.

    If the token's kid is not in the cached keys, the cache is busted
    and the JWKS is re-fetched once — handles Apple key rotations
    without requiring a server restart.
    """
    try:
        import jwt
        from jwt.algorithms import RSAAlgorithm

        jwks = await _get_apple_jwks()
        kid = jwt.get_unverified_header(id_token).get("kid")
        key_data = next((k for k in jwks if k["kid"] == kid), None)

        if not key_data:
            # kid not in cached keys — bust and retry once
            _apple_jwks_cache.clear()
            jwks = await _get_apple_jwks()
            key_data = next((k for k in jwks if k["kid"] == kid), None)
            if not key_data:
                raise ValueError("Apple signing key not found in JWKS after cache refresh")

        def _sync_decode() -> dict:
            public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
            return jwt.decode(
                id_token,
                public_key,
                algorithms=["RS256"],
                audience=settings.APPLE_CLIENT_ID,
                issuer="https://appleid.apple.com",
            )

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_decode)

    except Exception as exc:
        logger.warning(
            "Apple ID token verification failed",
            extra={"error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Apple token: {exc}",
        )


# ── Signup (3-step) ────────────────────────────────────────────────────────────

@router.post(
    "/signup/otp/request",
    status_code=status.HTTP_200_OK,
    summary="Step 1 — request an email OTP for signup",
)
async def signup_otp_request(
    payload: OtpRequestSchema,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a 6-digit OTP and email it. Always returns 200 even if the
    address already has an account — avoids leaking registered emails.
    Real duplicate check happens at the final /auth/signup step.

    Rate-limited at the reverse-proxy / middleware layer.
    TODO: wire slowapi or equivalent if middleware rate limiting is not in place.
    """
    async with request_guard(db, "/auth/signup/otp/request", email=payload.email):
        await request_otp(db, payload.email, purpose="verification")
        await db.commit()

    # Always return 200 regardless of whether an OtpError was raised above —
    # request_guard re-raises OtpError as HTTPException, so reaching here means
    # success. Anti-enumeration: the same message whether the email exists or not.
    return {"message": "If that address is valid, a verification code has been sent."}


@router.post(
    "/signup/otp/verify",
    status_code=status.HTTP_200_OK,
    summary="Step 2 — verify OTP, receive email_verification_token",
)
async def signup_otp_verify(
    payload: OtpVerifySchema,
    db: AsyncSession = Depends(get_db),
):
    """
    Verify the 6-digit OTP. On success, returns email_verification_token.
    Pass that token into /auth/signup as proof the email was verified.
    """
    async with request_guard(db, "/auth/signup/otp/verify", email=payload.email):
        token = await verify_otp(db, payload.email, payload.code)
        await db.commit()
        return {"email_verification_token": token}


@router.get(
    "/signup/check/business-name",
    status_code=status.HTTP_200_OK,
    summary="Check if a business name is available",
)
async def signup_check_business_name(
    name: str = Query(..., min_length=2, max_length=255),
    db: AsyncSession = Depends(get_db),
):
    """
    Fires on the business name field's input event (debounced ~400ms).
    Returns {"available": true/false} — no other data.

    The hard uniqueness guard is in org_owner_signup(); this is UI feedback only.
    """
    available = await check_business_name_available(db, name)
    return {"available": available}


@router.get(
    "/signup/check/phone",
    status_code=status.HTTP_200_OK,
    summary="Check if a phone number is available",
)
async def signup_check_phone(
    number: str = Query(..., min_length=7, max_length=20),
    db: AsyncSession = Depends(get_db),
):
    """
    Fires on the phone number field's input event (debounced ~400ms).
    Returns {"available": true/false} — no other data.

    The hard uniqueness guard is in org_owner_signup(); this is UI feedback only.
    """
    available = await check_phone_available(db, number)
    return {"available": available}


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Step 3 — org owner self-registration",
)
async def signup(
    payload: OrgOwnerRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new organization and its owner account atomically.
    Returns a JWT pair immediately — no separate login step needed.

    Email is recovered from email_verification_token server-side.
    Slug is generated from business_name server-side — not user-supplied.
    Response includes the generated slug so the frontend can display the org URL.
    """
    d = _device_meta(request)
    async with request_guard(db, "/auth/signup"):
        tokens = await org_owner_signup(
            session=db,
            email_verification_token=payload.email_verification_token,
            first_name=payload.first_name,
            last_name=payload.last_name,
            password=payload.password,
            business_name=payload.business_name,
            phone_number=payload.phone_number,
            employee_count=payload.employee_count,
            organization_type=payload.organization_type,
            work_model=payload.work_model,
            device_name=d.device_name,
            device_fingerprint=d.device_fingerprint,
            ip_address=d.ip_address,
        )
        await db.commit()
        return tokens


@router.post(
    "/invite/otp/request",
    status_code=status.HTTP_200_OK,
    summary="Invite Step 1 — send OTP to invite email",
)
async def invite_otp_request(
    payload: InviteOtpRequestSchema,
    db: AsyncSession = Depends(get_db),
):
    """
    Validate the invite code and send a 6-digit OTP to the invited email.
    Returns the masked email so the frontend can display where the code went —
    the client never sends or sees the raw email at this step.
    """
    async with request_guard(
        db,
        "/auth/invite/otp/request",
        invite_code=payload.invite_code,
    ):
        masked_email = await request_invite_otp(db, payload.invite_code)
        await db.commit()
        return {
            "message": "Verification code sent.",
            "sent_to": masked_email,  # e.g. "j***@example.com"
        }


@router.post(
    "/invite/otp/verify",
    status_code=status.HTTP_200_OK,
    summary="Invite Step 2 — verify OTP, receive invite_verification_token",
)
async def invite_otp_verify(
    payload: InviteOtpVerifySchema,
    db: AsyncSession = Depends(get_db),
):
    """
    Verify the 6-digit OTP for an invite.
    Returns invite_verification_token — pass this into /auth/register/invite.
    """
    async with request_guard(
        db,
        "/auth/invite/otp/verify",
        invite_code=payload.invite_code,
    ):
        token = await verify_invite_otp(db, payload.invite_code, payload.code)
        await db.commit()
        return {"invite_verification_token": token}


@router.post(
    "/register/invite",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite Step 3 — complete registration with wellbeing baseline",
)
async def register_invited(
    payload: InviteRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Complete invite registration. Email and invite_code are decoded
    server-side from invite_verification_token — client supplies neither.

    Creates the user account, seeds the wellbeing baseline (first
    DailyCheckin), priorities, and activity feed entry atomically.
    """
    d = _device_meta(request)
    async with request_guard(db, "/auth/register/invite"):
        tokens = await invite_register(
            session=db,
            invite_verification_token=payload.invite_verification_token,
            first_name=payload.first_name,
            last_name=payload.last_name,
            password=payload.password,
            country=payload.country,
            state=payload.state,
            baseline=payload.baseline.model_dump(),
            priorities=[p.model_dump() for p in payload.priorities],
            device_name=d.device_name,
            device_fingerprint=d.device_fingerprint,
            ip_address=d.ip_address,
        )
        await db.commit()
        return tokens


# ── Login ──────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    summary="Email + password login",
    # The response is a discriminated union: TokenResponse when 2FA is off,
    # TwoFaChallengeResponse when 2FA is on. FastAPI's response_model does not
    # natively support a runtime-chosen union, so we type-validate the dict
    # returned by password_login() against the correct schema before returning
    # it, then send it as a pre-serialised JSONResponse. This means:
    #   • The OpenAPI schema advertises both shapes (oneOf).
    #   • Shape validation happens here in the router, not silently skipped.
    #   • No unvalidated dicts ever reach the client.
    responses={
        200: {
            "description": "Token pair (2FA disabled) or 2FA challenge token",
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {"$ref": "#/components/schemas/TokenResponse"},
                            {"$ref": "#/components/schemas/TwoFaChallengeResponse"},
                        ]
                    }
                }
            },
        }
    },
)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with email and password.

    Returns:
      • TokenResponse          — when 2FA is disabled (normal path)
      • TwoFaChallengeResponse — when 2FA is enabled; client must then
                                 call POST /auth/2fa/verify with the
                                 challenge token + their 2FA code.

    Uses JSON body (not OAuth2 form) — friendlier for mobile clients.

    Rate-limited at the reverse-proxy / middleware layer.
    TODO: wire slowapi or equivalent if middleware rate limiting is not in place.
    """
    d = _device_meta(request)
    async with request_guard(db, "/auth/login", email=payload.email):
        result = await password_login(
            session=db,
            email=payload.email,
            password=payload.password,
            device_name=d.device_name,
            device_fingerprint=d.device_fingerprint,
            ip_address=d.ip_address,
        )
        await db.commit()

        # Validate the returned dict against the correct schema so shape
        # regressions in the service layer are caught at the router boundary.
        if "two_fa_challenge_token" in result:
            validated = TwoFaChallengeResponse.model_validate(result)
        else:
            validated = TokenResponse.model_validate(result)

        return JSONResponse(content=validated.model_dump())


@router.post(
    "/login/google",
    response_model=TokenResponse,
    summary="Google OAuth login (frontend-first)",
)
async def login_google(
    payload: OAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Frontend sends Google ID token → server verifies → JWT pair issued.
    Name and avatar are extracted from the verified token claims.
    New users require invite_code; existing linked accounts can omit it.
    first_name / last_name in the payload are ignored for Google.
    """
    await _verify_google(payload.id_token)  # pre-flight, non-blocking
    d = _device_meta(request)
    async with request_guard(db, "/auth/login/google"):
        tokens = await oauth_login(
            session=db,
            provider="google",
            id_token=payload.id_token,
            invite_code=payload.invite_code,
            device_name=d.device_name,
            device_fingerprint=d.device_fingerprint,
            ip_address=d.ip_address,
        )
        await db.commit()
        return tokens


@router.post(
    "/login/apple",
    response_model=TokenResponse,
    summary="Apple Sign In (frontend-first)",
)
async def login_apple(
    payload: OAuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Frontend sends Apple ID token → server verifies → JWT pair issued.

    Apple only provides the user's name on the VERY FIRST login via the
    native SDK credential object — it is NOT in the ID token.
    The frontend must capture first_name / last_name at that moment and
    include them in this payload. On subsequent logins those fields may
    be omitted (the user already exists in the DB).
    """
    await _verify_apple(payload.id_token)  # pre-flight, non-blocking + cached JWKS
    d = _device_meta(request)
    async with request_guard(db, "/auth/login/apple"):
        tokens = await oauth_login(
            session=db,
            provider="apple",
            id_token=payload.id_token,
            invite_code=payload.invite_code,
            first_name=payload.first_name,
            last_name=payload.last_name,
            device_name=d.device_name,
            device_fingerprint=d.device_fingerprint,
            ip_address=d.ip_address,
        )
        await db.commit()
        return tokens


# ── 2FA — challenge verification ──────────────────────────────────────────────

@router.post(
    "/2fa/verify",
    response_model=TokenResponse,
    summary="Complete a 2FA challenge after password login",
)
async def two_fa_verify(
    payload: TwoFaVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Second step when POST /auth/login returns TwoFaChallengeResponse.

    Submits the 6-digit TOTP / SMS / email code together with the
    challenge token that was returned by the login endpoint. On success,
    issues the full JWT pair (same shape as a normal login response).

    The challenge token encodes the user_id + device meta and is
    short-lived (5 min). It is single-use — consuming it here marks it
    spent; replaying it returns 401.

    Rate-limited at the reverse-proxy / middleware layer.
    TODO: wire slowapi or equivalent if middleware rate limiting is not in place.
    """
    d = _device_meta(request)
    async with request_guard(db, "/auth/2fa/verify"):
        tokens = await verify_two_fa_challenge(
            session=db,
            challenge_token=payload.two_fa_challenge_token,
            code=payload.code,
            device_name=d.device_name,
            device_fingerprint=d.device_fingerprint,
            ip_address=d.ip_address,
        )
        await db.commit()
        return tokens


# ── 2FA — setup / disable (authenticated) ─────────────────────────────────────

@router.post(
    "/2fa/setup",
    response_model=TwoFaSetupResponse,
    summary="Initiate 2FA enrollment for the authenticated user",
)
async def two_fa_setup(
    payload: TwoFaSetupRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Initiate 2FA setup.

    method="totp":
      Returns totp_secret (base32) and totp_uri (otpauth:// string for
      a QR code). 2FA is NOT active until the user calls
      POST /auth/2fa/verify with a valid first code — confirming they
      have the secret stored correctly in their authenticator app.

    method="sms":
      phone_number is required. An OTP is sent immediately. Call
      POST /auth/2fa/verify to activate.

    method="email":
      OTP sent to the user's registered email. Call /auth/2fa/verify
      to activate.
    """
    if payload.method == "sms" and not payload.phone_number:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="phone_number is required when method is 'sms'.",
        )
    async with request_guard(db, "/auth/2fa/setup", user_id=str(current_user.user_id)):
        result = await initiate_two_fa_setup(
            session=db,
            user_id=current_user.user_id,
            method=payload.method,
            phone_number=payload.phone_number,
        )
        await db.commit()
        return result


@router.post(
    "/2fa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable 2FA for the authenticated user",
)
async def two_fa_disable(
    payload: TwoFaDisableRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Disable 2FA. Requires the user's current password as confirmation
    so a session-hijacker can't silently downgrade account security.
    """
    async with request_guard(db, "/auth/2fa/disable", user_id=str(current_user.user_id)):
        await disable_two_fa(
            session=db,
            user_id=current_user.user_id,
            password=payload.password,
        )
        await db.commit()


# ── Session ────────────────────────────────────────────────────────────────────

@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Refresh access token",
)
async def refresh(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Rotate the refresh token and return a new access + refresh pair.

    Each refresh token is single-use — token_service.rotate_refresh_token()
    overwrites the stored hash immediately, so a stolen token can only be
    replayed once before the legitimate user's next refresh invalidates it.

    Permissions are re-loaded from DB on every rotation so role/permission
    changes propagate without requiring a full re-login.
    """
    async with request_guard(db, "/auth/refresh"):
        result = await refresh_access_token(
            session=db,
            raw_refresh_token=payload.refresh_token,
        )
        await db.commit()
        return result


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke refresh token (logout this device)",
)
async def logout_route(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke the refresh token for this device session.
    Access token remains valid until its natural expiry.
    Add a Redis jti blocklist to close that window immediately if needed.
    """
    async with request_guard(db, "/auth/logout"):
        await logout(session=db, raw_refresh_token=payload.refresh_token)
        await db.commit()


@router.get(
    "/me",
    response_model=UserMeResponse,
    summary="Current user identity, permissions, and 2FA status",
)
async def me(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the authenticated user's full identity including:
      • role (display label — independent of permissions)
      • permissions (directly granted via UserPermission)
      • 2FA status and method (from UserSettings)
      • avatar_url (for OAuth users)

    role and permissions are loaded by get_current_user and are already fresh.
    get_me() hits the DB once more only for the columns CurrentUser doesn't
    carry (name, avatar_url, 2FA state).
    """
    async with request_guard(db, "/auth/me", user_id=str(current_user.user_id)):
        user = await get_me(db, current_user.user_id)
        settings_row = user.settings
        return UserMeResponse(
            user_id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            role=current_user.role,
            organization_id=current_user.organization_id,
            branch_id=current_user.branch_id,
            permissions=current_user.permissions,
            avatar_url=user.avatar_url,
            two_fa_enabled=settings_row.two_fa_enabled if settings_row else False,
            two_fa_method=settings_row.two_fa_method if settings_row else None,
        )