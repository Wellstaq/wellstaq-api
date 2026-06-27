"""
app/services/two_factor_service.py

All 2FA business logic:
  - TOTP secret generation and verification (stdlib only, no pyotp)
  - initiate_two_fa_setup   — store pending secret, send OTP for SMS/email
  - confirm_two_fa_setup    — verify first code, activate 2FA
  - issue_two_fa_challenge  — short-lived challenge token after password login
  - verify_two_fa_challenge — verify code, issue full JWT pair
  - disable_two_fa          — requires current password

Services never commit — the router owns the transaction boundary.
"""

from __future__ import annotations

import base64
import hmac
import logging
import struct
import time
from datetime import timedelta
from typing import Literal

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import TokenError, decode_token, verify_password
from app.core.constants import TwoFaMethod, TokenType
from app.models import User, UserSettings
from .token_service import issue_tokens, two_fa_challenge_response

logger = logging.getLogger(__name__)


# ── AuthError — lazy import to avoid circular dependency ───────────────────────
# two_factor_service → app.services.auth.errors (AuthError)
# app.services.auth  → two_factor_service (issue_two_fa_challenge, etc.)
# Lazy import at call time breaks the cycle cleanly.

def _auth_error(message: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> Exception:
    """
    Lazy factory for AuthError.
    Imports from the new package path (app.services.auth.errors),
    not the old monolith (app.services.auth_service).
    """
    from app.services.auth.errors import AuthError
    return AuthError(message, status_code)


# ── Challenge token ────────────────────────────────────────────────────────────

def _create_challenge_token(
    user_id: str,
    method: str,
    device_name: str | None,
    device_fingerprint: str | None,
    ip_address: str | None,
) -> str:
    """
    Create a 5-minute short-lived token encoding the 2FA challenge context.

    Uses jwt.py's private _create_token() because create_access_token()
    doesn't accept custom expiry or extra claims.

    TODO: add create_custom_token() to jwt.py as a clean public surface:
        def create_custom_token(subject, extra_claims, expires_delta) -> str:
            return _create_token(subject, expires_delta, "access", extra_claims)
    """
    from app.auth import jwt as _jwt_module
    return _jwt_module._create_token(
        subject=user_id,
        expires_delta=timedelta(minutes=5),
        token_type="access",
        extra_claims={
            "type": TokenType.TWO_FA_CHALLENGE,
            "two_fa_method": method,
            "device_name": device_name,
            "device_fingerprint": device_fingerprint,
            "ip_address": ip_address,
        },
    )


# ── TOTP primitives (stdlib only — no pyotp dependency) ───────────────────────

def generate_totp_secret() -> str:
    """Generate a 20-byte cryptographically random TOTP secret, base32-encoded."""
    import secrets
    return base64.b32encode(secrets.token_bytes(20)).decode()


def totp_uri(secret: str, email: str, issuer: str = "Wellstaq") -> str:
    from urllib.parse import quote
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(email)}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """
    Verify a 6-digit TOTP code against a clean base32 secret.
    Checks ±window 30-second steps to accommodate clock skew.
    """
    try:
        key = base64.b32decode(secret.upper())
    except Exception:
        return False

    def _hotp(k: bytes, counter: int) -> str:
        msg = struct.pack(">Q", counter)
        h = hmac.new(k, msg, "sha1").digest()
        offset = h[-1] & 0x0F
        code_int = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
        return str(code_int % 1_000_000).zfill(6)

    counter = int(time.time()) // 30
    return any(
        hmac.compare_digest(_hotp(key, counter + delta), code.strip())
        for delta in range(-window, window + 1)
    )


# ── Pending-prefix helpers ─────────────────────────────────────────────────────

_PENDING = "pending:"


def _make_pending(value: str) -> str:
    return f"{_PENDING}{value}"


def _strip_pending(raw: str) -> str | None:
    """Return the clean value if raw has the pending prefix, else None."""
    return raw[len(_PENDING):] if raw.startswith(_PENDING) else None


# ── Shared loader ──────────────────────────────────────────────────────────────

async def _load_user_with_settings(
    session: AsyncSession, user_id: str
) -> tuple[User, UserSettings]:
    result = await session.execute(
        select(User)
        .options(selectinload(User.settings))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise _auth_error("User not found.", status.HTTP_404_NOT_FOUND)
    settings_row: UserSettings | None = user.settings
    if not settings_row:
        raise _auth_error("User settings not found.", status.HTTP_500_INTERNAL_SERVER_ERROR)
    return user, settings_row


# ── 1. Issue 2FA challenge ─────────────────────────────────────────────────────

async def issue_two_fa_challenge(
    user: User,
    method: str,
    device_name: str | None,
    device_fingerprint: str | None,
    ip_address: str | None,
) -> dict:
    """
    Create a short-lived challenge token after password login.
    Client exchanges it at /auth/2fa/verify.
    """
    challenge_token = _create_challenge_token(
        user_id=user.id,
        method=method,
        device_name=device_name,
        device_fingerprint=device_fingerprint,
        ip_address=ip_address,
    )
    return two_fa_challenge_response(challenge_token, method)


# ── 2. Initiate 2FA setup ──────────────────────────────────────────────────────

async def initiate_two_fa_setup(
    session: AsyncSession,
    user_id: str,
    method: Literal["totp", "sms", "email"],
    phone_number: str | None = None,
) -> dict:
    """
    Begin 2FA enrollment. Stores a pending secret; does NOT enable 2FA yet.
    Client must call confirm_two_fa_setup() with a valid first code to activate.
    """
    user, settings_row = await _load_user_with_settings(session, user_id)

    if settings_row.two_fa_enabled:
        raise _auth_error(
            "2FA is already enabled. Disable it first before changing method.",
            status.HTTP_409_CONFLICT,
        )

    if method == TwoFaMethod.TOTP:
        secret = generate_totp_secret()
        uri = totp_uri(secret, user.email)
        settings_row.two_fa_secret = _make_pending(secret)
        await session.flush()
        return {"method": TwoFaMethod.TOTP, "totp_secret": secret, "totp_uri": uri}

    elif method == TwoFaMethod.SMS:
        if not phone_number:
            raise _auth_error(
                "phone_number is required for SMS 2FA.",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        from app.services.otp_service import request_otp
        user.phone_number = phone_number
        try:
            await request_otp(session, user.email, purpose="2fa_sms")
        except Exception as exc:
            raise _auth_error(
                "Failed to send SMS verification code. Please try again.",
                status.HTTP_502_BAD_GATEWAY,
            ) from exc
        settings_row.two_fa_secret = _make_pending(f"sms:{phone_number}")
        await session.flush()
        return {"method": TwoFaMethod.SMS, "totp_secret": None, "totp_uri": None}

    elif method == TwoFaMethod.EMAIL:
        from app.services.otp_service import request_otp
        await request_otp(session, user.email, purpose="2fa_email")
        settings_row.two_fa_secret = _make_pending("email")
        await session.flush()
        return {"method": TwoFaMethod.EMAIL, "totp_secret": None, "totp_uri": None}

    else:
        raise _auth_error(f"Unknown 2FA method '{method}'.", status.HTTP_400_BAD_REQUEST)


# ── 3. Confirm 2FA setup ───────────────────────────────────────────────────────

async def confirm_two_fa_setup(
    session: AsyncSession,
    user_id: str,
    code: str,
) -> None:
    """
    Verify the user's first live code and fully activate 2FA.

    Without this step TOTP would never activate — verify_totp() would
    receive "pending:BASE32SECRET" which is invalid base32 and always fails.

    After this call:
      two_fa_secret  → clean (pending prefix stripped)
      two_fa_method  → set
      two_fa_enabled → True
    """
    user, settings_row = await _load_user_with_settings(session, user_id)

    if settings_row.two_fa_enabled:
        raise _auth_error("2FA is already active on this account.", status.HTTP_409_CONFLICT)

    raw_pending = settings_row.two_fa_secret or ""
    clean = _strip_pending(raw_pending)
    if not clean:
        raise _auth_error(
            "No pending 2FA setup found. Call initiate_two_fa_setup() first.",
            status.HTTP_400_BAD_REQUEST,
        )

    if clean.startswith("sms:"):
        method = TwoFaMethod.SMS
    elif clean == "email":
        method = TwoFaMethod.EMAIL
    else:
        method = TwoFaMethod.TOTP

    if method == TwoFaMethod.TOTP:
        if not verify_totp(clean, code):
            raise _auth_error(
                "Invalid TOTP code. Check your authenticator app and try again.",
                status.HTTP_401_UNAUTHORIZED,
            )
        settings_row.two_fa_secret = clean

    elif method in (TwoFaMethod.SMS, TwoFaMethod.EMAIL):
        from app.services.otp_service import OtpError, verify_otp
        try:
            await verify_otp(session, user.email, code)
        except OtpError as exc:
            raise _auth_error(exc.message, exc.status_code) from exc
        settings_row.two_fa_secret = clean

    settings_row.two_fa_method = method
    settings_row.two_fa_enabled = True
    await session.flush()


# ── 4. Verify 2FA challenge ────────────────────────────────────────────────────

async def verify_two_fa_challenge(
    session: AsyncSession,
    challenge_token: str,
    code: str,
    device_name: str | None = None,
    device_fingerprint: str | None = None,
    ip_address: str | None = None,
) -> dict:
    """
    Complete a 2FA challenge and issue a full JWT pair.

    Device metadata embedded in the challenge token takes precedence —
    prevents a client from swapping device context between challenge and verify.
    """
    try:
        payload = decode_token(challenge_token)
    except TokenError as exc:
        raise _auth_error(
            "2FA challenge token is invalid or expired. Please log in again.",
            status.HTTP_401_UNAUTHORIZED,
        ) from exc

    if payload.get("type") != TokenType.TWO_FA_CHALLENGE:
        raise _auth_error("Invalid challenge token.", status.HTTP_401_UNAUTHORIZED)

    user_id = payload.get("sub")
    method = payload.get("two_fa_method")
    resolved_device_name = payload.get("device_name") or device_name
    resolved_fp = payload.get("device_fingerprint") or device_fingerprint
    resolved_ip = payload.get("ip_address") or ip_address

    if not user_id or not method:
        raise _auth_error("Malformed challenge token.", status.HTTP_401_UNAUTHORIZED)

    user, settings_row = await _load_user_with_settings(session, user_id)

    if not settings_row.two_fa_enabled:
        raise _auth_error(
            "2FA is not enabled on this account. Please log in again.",
            status.HTTP_400_BAD_REQUEST,
        )

    if method == TwoFaMethod.TOTP:
        if not settings_row.two_fa_secret:
            raise _auth_error("TOTP is not configured.", status.HTTP_400_BAD_REQUEST)
        if not verify_totp(settings_row.two_fa_secret, code):
            raise _auth_error("Invalid or expired TOTP code.", status.HTTP_401_UNAUTHORIZED)

    elif method in (TwoFaMethod.EMAIL, TwoFaMethod.SMS):
        from app.services.otp_service import OtpError, verify_otp
        try:
            await verify_otp(session, user.email, code)
        except OtpError as exc:
            raise _auth_error(exc.message, exc.status_code) from exc

    else:
        raise _auth_error(f"Unknown 2FA method '{method}'.", status.HTTP_400_BAD_REQUEST)

    return await issue_tokens(session, user, resolved_device_name, resolved_fp, resolved_ip)


# ── 5. Disable 2FA ─────────────────────────────────────────────────────────────

async def disable_two_fa(
    session: AsyncSession,
    user_id: str,
    password: str,
) -> None:
    """
    Disable 2FA. Requires current password to prevent silent security
    downgrade by a session hijacker.
    """
    user, settings_row = await _load_user_with_settings(session, user_id)

    if not user.hashed_password:
        raise _auth_error(
            "This account uses social login and has no password.",
            status.HTTP_400_BAD_REQUEST,
        )
    if not verify_password(password, user.hashed_password):
        raise _auth_error("Incorrect password.", status.HTTP_401_UNAUTHORIZED)

    if not settings_row.two_fa_enabled:
        raise _auth_error("2FA is not currently enabled.", status.HTTP_400_BAD_REQUEST)

    settings_row.two_fa_enabled = False
    settings_row.two_fa_method = None
    settings_row.two_fa_secret = None
    await session.flush()