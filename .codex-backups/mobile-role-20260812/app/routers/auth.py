"""
app/routers/auth.py

Authentication routes.

Endpoints
─────────
Signup (3-step — org owner only):
  POST /auth/signup/otp/request  — Step 1: send email OTP
  POST /auth/signup/otp/verify   — Step 2: verify OTP → email_verification_token
  POST /auth/signup              — Step 3: create org + owner (status=ACTIVE)

Invite (employee — password only):
  POST /auth/invite/otp/request  — Step 1: validate invite, send OTP
  POST /auth/invite/otp/verify   — Step 2: verify OTP → invite_verification_token
  POST /auth/register/invite     — Step 3: create account + baseline (status=ACTIVE)

Login:
  POST /auth/login               — email + password
  POST /auth/login/google        — existing user OAuth (Gmail auto-link)
  POST /auth/login/apple         — existing user OAuth (must pre-link in settings)

2FA:
  POST /auth/2fa/verify          — complete challenge → full JWT
  POST /auth/2fa/setup           — initiate enrollment
  POST /auth/2fa/disable         — disable 2FA

Session:
  POST /auth/refresh             — rotate refresh token
  POST /auth/logout              — revoke refresh token
  GET  /auth/me                  — identity + permissions + 2FA status

OAuth design note:
  OAuth is for EXISTING users only — no registration via OAuth.
  Google: auto-links if the account email is @gmail.com.
  Apple:  requires explicit linking in account settings first.
  Neither endpoint accepts invite_code anymore.
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.auth import (
    AccessTokenResponse,
    InviteOtpRequestSchema,
    InviteOtpVerifySchema,
    InviteRegisterRequest,
    LoginRequest,
    OAuthRequest,
    OrgOwnerRegisterRequest,
    OtpRequestSchema,
    OtpVerifySchema,
    RefreshRequest,
    TokenResponse,
    TwoFaChallengeResponse,
    TwoFaDisableRequest,
    TwoFaConfirmRequest,
    SessionItemResponse,
    SessionListResponse,
    TwoFaSetupRequest,
    TwoFaSetupResponse,
    TwoFaVerifyRequest,
    UserMeResponse,
)
from app.services.auth import (
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
from app.services.errors import AuthError
from app.services.auth.two_factor_service import confirm_two_fa_setup
from app.models import UserDevice
from app.core.limiter import limiter, key_by_email_or_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Typed device metadata ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DeviceMeta:
    """
    Immutable per-request device context.
    ip_address excluded from fingerprint — see _device_fingerprint docstring.
    """
    device_name: str | None
    device_fingerprint: str | None
    ip_address: str | None


def _device_fingerprint(request: Request) -> str | None:
    """
    Stable session identifier from client headers. Full SHA-256 (no truncation).
    IP deliberately excluded: mobile users on cellular change IPs mid-session
    via carrier NAT, which would invalidate their device scope.
    """
    user_agent = request.headers.get("user-agent", "")
    x_device_id = request.headers.get("x-device-id", "")
    if not user_agent and not x_device_id:
        return None
    return hashlib.sha256(f"{user_agent}|{x_device_id}".encode()).hexdigest()


def _device_meta(request: Request) -> DeviceMeta:
    return DeviceMeta(
        device_name=request.headers.get("x-device-name"),
        device_fingerprint=_device_fingerprint(request),
        ip_address=request.client.host if request.client else None,
    )


# ── Google token verification ──────────────────────────────────────────────────

async def _verify_google(id_token: str) -> dict:
    """
    Verify a Google ID token and return verified claims.
    Runs in executor — google.auth is blocking (network + RSA CPU).
    Returns the claims dict; caller passes it to oauth_login() directly
    so the service never re-verifies.
    """
    def _sync_verify() -> dict:
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token as g_id_token
        return g_id_token.verify_oauth2_token(
            id_token, g_requests.Request(), settings.GOOGLE_CLIENT_ID,
        )

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _sync_verify)
    except Exception as exc:
        logger.warning("Google ID token verification failed", extra={"error": str(exc)})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid Google token: {exc}")


# ── Apple token verification with JWKS caching ────────────────────────────────

_apple_jwks_cache: dict = {}
_APPLE_JWKS_TTL = 3600


async def _get_apple_jwks() -> list[dict]:
    now = time.monotonic()
    cached = _apple_jwks_cache.get("keys")
    if cached and (now - _apple_jwks_cache.get("fetched_at", 0.0)) < _APPLE_JWKS_TTL:
        return cached
    import httpx
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get("https://appleid.apple.com/auth/keys")
        resp.raise_for_status()
        keys = resp.json()["keys"]
    _apple_jwks_cache.update({"keys": keys, "fetched_at": now})
    return keys


async def _verify_apple(id_token: str) -> dict:
    """
    Verify an Apple ID token and return verified claims.
    JWKS cached (1h TTL). RSA decode runs in executor.
    Returns claims dict for direct pass-through to oauth_login().
    """
    try:
        import jwt
        from jwt.algorithms import RSAAlgorithm

        jwks = await _get_apple_jwks()
        kid = jwt.get_unverified_header(id_token).get("kid")
        key_data = next((k for k in jwks if k["kid"] == kid), None)

        if not key_data:
            _apple_jwks_cache.clear()
            jwks = await _get_apple_jwks()
            key_data = next((k for k in jwks if k["kid"] == kid), None)
            if not key_data:
                raise ValueError("Apple signing key not found in JWKS after cache refresh")

        def _sync_decode() -> dict:
            public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
            return jwt.decode(
                id_token, public_key, algorithms=["RS256"],
                audience=settings.APPLE_CLIENT_ID, issuer="https://appleid.apple.com",
            )

        return await asyncio.get_running_loop().run_in_executor(None, _sync_decode)

    except Exception as exc:
        logger.warning("Apple ID token verification failed", extra={"error": str(exc)})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid Apple token: {exc}")


# ── Signup (3-step — org owner) ────────────────────────────────────────────────

@router.post("/signup/otp/request", status_code=status.HTTP_200_OK,
             summary="Step 1 — request an email OTP for signup")
@limiter.limit("3/minute", key_func=key_by_email_or_ip)
async def signup_otp_request(request: Request, payload: OtpRequestSchema, db: AsyncSession = Depends(get_db)):
    async with request_guard(db, "/auth/signup/otp/request", email=payload.email):
        await request_otp(db, payload.email, purpose="verification")
        await db.commit()
    return {"message": "If that address is valid, a verification code has been sent."}


@router.post("/signup/otp/verify", status_code=status.HTTP_200_OK,
             summary="Step 2 — verify OTP, receive email_verification_token")
async def signup_otp_verify(payload: OtpVerifySchema, db: AsyncSession = Depends(get_db)):
    async with request_guard(db, "/auth/signup/otp/verify", email=payload.email):
        token = await verify_otp(db, payload.email, payload.code, "verification")
        await db.commit()
        return {"email_verification_token": token}


@router.get("/signup/check/business-name", status_code=status.HTTP_200_OK,
            summary="Check if a business name is available")
async def signup_check_business_name(
    name: str = Query(..., min_length=2, max_length=255),
    db: AsyncSession = Depends(get_db),
):
    return {"available": await check_business_name_available(db, name)}


@router.get("/signup/check/phone", status_code=status.HTTP_200_OK,
            summary="Check if a phone number is available")
async def signup_check_phone(
    number: str = Query(..., min_length=7, max_length=20),
    db: AsyncSession = Depends(get_db),
):
    return {"available": await check_phone_available(db, number)}


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED,
             summary="Step 3 — org owner self-registration")
async def signup(payload: OrgOwnerRegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Creates org + owner atomically. Owner is ACTIVE immediately.
    Returns tokens + slug for the new organization.
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


# ── Invite (employee registration) ────────────────────────────────────────────

@router.post("/invite/otp/request", status_code=status.HTTP_200_OK,
             summary="Invite Step 1 — send OTP to invite email")
async def invite_otp_request(payload: InviteOtpRequestSchema, db: AsyncSession = Depends(get_db)):
    async with request_guard(db, "/auth/invite/otp/request", invite_code=payload.invite_code):
        masked_email = await request_invite_otp(db, payload.invite_code)
        await db.commit()
        return {"message": "Verification code sent.", "sent_to": masked_email}


@router.post("/invite/otp/verify", status_code=status.HTTP_200_OK,
             summary="Invite Step 2 — verify OTP, receive invite_verification_token")
async def invite_otp_verify(payload: InviteOtpVerifySchema, db: AsyncSession = Depends(get_db)):
    async with request_guard(db, "/auth/invite/otp/verify", invite_code=payload.invite_code):
        token = await verify_invite_otp(db, payload.invite_code, payload.code)
        await db.commit()
        return {"invite_verification_token": token}


@router.post("/register/invite", response_model=TokenResponse, status_code=status.HTTP_201_CREATED,
             summary="Invite Step 3 — complete registration with wellbeing baseline")
async def register_invited(payload: InviteRegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Creates employee account + wellbeing baseline atomically.
    User is PENDING during the transaction, activated to ACTIVE before tokens are issued.
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
@limiter.limit("10/minute", key_func=key_by_email_or_ip)
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Returns TokenResponse (2FA off) or TwoFaChallengeResponse (2FA on).
    Rate-limited at the reverse-proxy / middleware layer.
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

        if "two_fa_challenge_token" in result:
            validated = TwoFaChallengeResponse.model_validate(result)
        else:
            validated = TokenResponse.model_validate(result)

        return JSONResponse(content=validated.model_dump())


@router.post("/login/google", response_model=TokenResponse,
             summary="Google OAuth login — existing users only (Gmail auto-links)")
async def login_google(payload: OAuthRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Existing users only. No registration via OAuth.
    Gmail accounts are auto-linked on first OAuth login.
    Non-Gmail Google accounts are rejected.
    """
    claims = await _verify_google(payload.id_token)
    d = _device_meta(request)
    async with request_guard(db, "/auth/login/google"):
        tokens = await oauth_login(
            session=db,
            provider="google",
            claims=claims,
            # No invite_code — OAuth is not a registration path
            device_name=d.device_name,
            device_fingerprint=d.device_fingerprint,
            ip_address=d.ip_address,
        )
        await db.commit()
        return tokens


@router.post("/login/apple", response_model=TokenResponse,
             summary="Apple Sign In — existing users with Apple pre-linked only")
async def login_apple(payload: OAuthRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Existing users only. Apple must be linked in account settings first.
    No auto-linking for Apple (private relay emails are unreliable for matching).
    No registration via OAuth.
    """
    claims = await _verify_apple(payload.id_token)
    d = _device_meta(request)
    async with request_guard(db, "/auth/login/apple"):
        tokens = await oauth_login(
            session=db,
            provider="apple",
            claims=claims,
            # No invite_code, no first_name/last_name — existing user only
            device_name=d.device_name,
            device_fingerprint=d.device_fingerprint,
            ip_address=d.ip_address,
        )
        await db.commit()
        return tokens


# ── 2FA ────────────────────────────────────────────────────────────────────────

@router.post("/2fa/verify", response_model=TokenResponse,
             summary="Complete a 2FA challenge after password login")
@limiter.limit("5/minute", key_func=key_by_email_or_ip)            
async def two_fa_verify(payload: TwoFaVerifyRequest, request: Request, db: AsyncSession = Depends(get_db)):
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


@router.post("/2fa/setup", response_model=TwoFaSetupResponse,
             summary="Initiate 2FA enrollment for the authenticated user")
async def two_fa_setup(
    payload: TwoFaSetupRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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


@router.post("/2fa/disable", status_code=status.HTTP_204_NO_CONTENT,
             summary="Disable 2FA for the authenticated user")
async def two_fa_disable(
    payload: TwoFaDisableRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    async with request_guard(db, "/auth/2fa/disable", user_id=str(current_user.user_id)):
        await disable_two_fa(session=db, user_id=current_user.user_id, password=payload.password)
        await db.commit()

@router.post("/2fa/confirm", status_code=status.HTTP_204_NO_CONTENT,
             summary="Confirm and activate a pending 2FA enrollment")
async def two_fa_confirm(
    payload: TwoFaConfirmRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    async with request_guard(db, "/auth/2fa/confirm", user_id=str(current_user.user_id)):
        await confirm_two_fa_setup(session=db, user_id=current_user.user_id, code=payload.code)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/sessions", response_model=SessionListResponse, summary="List active login sessions")
async def list_sessions(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    async with request_guard(db, "/auth/sessions", user_id=str(current_user.user_id)):
        rows = (await db.execute(
            select(UserDevice).where(UserDevice.user_id == current_user.user_id, UserDevice.revoked.is_(False))
            .order_by(UserDevice.last_seen_at.desc())
        )).scalars().all()
        return SessionListResponse(items=[
            SessionItemResponse(
                id=row.id,
                device_name=row.device_name,
                ip_address=row.ip_address,
                location_label=row.location_label,
                last_seen_at=row.last_seen_at,
                created_at=row.created_at,
                is_current=row.id == current_user.session_id,
            )
            for row in rows
        ])

@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke a login session")
async def revoke_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    async with request_guard(db, "/auth/sessions/{session_id}", user_id=str(current_user.user_id)):
        row = (await db.execute(select(UserDevice).where(
            UserDevice.id == session_id, UserDevice.user_id == current_user.user_id,
        ))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
        row.revoked = True
        row.refresh_token_hash = None
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Session ────────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=AccessTokenResponse, summary="Refresh access token")
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """
    Rotates the refresh token — each token is single-use.
    Permissions re-loaded from DB on every rotation.
    """
    async with request_guard(db, "/auth/refresh"):
        result = await refresh_access_token(session=db, raw_refresh_token=payload.refresh_token)
        await db.commit()
        return result


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT,
             summary="Revoke refresh token (logout this device)")
async def logout_route(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """
    Idempotent — silently succeeds if token already expired or unrecognised.
    Access token remains valid until natural expiry.
    """
    async with request_guard(db, "/auth/logout"):
        await logout(session=db, raw_refresh_token=payload.refresh_token)
        await db.commit()

@router.get("/me", response_model=UserMeResponse,
            summary="Current user identity, permissions, and 2FA status")
async def me(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    async with request_guard(db, "/auth/me", user_id=str(current_user.user_id)):
        user, department_id = await get_me(db, current_user.user_id, role=current_user.role)
        settings_row = user.settings
        return UserMeResponse(
            user_id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            role=current_user.role,
            organization_id=current_user.organization_id,
            branch_id=current_user.branch_id,
            phone_number=user.phone_number,
            location=user.country,
            department_id=department_id,
            status=user.status,          # <-- add this
            permissions=current_user.permissions,
            avatar_url=user.avatar_url,
            two_fa_enabled=settings_row.two_fa_enabled if settings_row else False,
            two_fa_method=settings_row.two_fa_method if settings_row else None,
        )
