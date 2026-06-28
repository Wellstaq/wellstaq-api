"""
app/services/auth/otp_service.py

Centralised OTP management.
Handles generation, hashing, storage, and delivery (email/SMS) of One-Time Passwords.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    OTP_LENGTH,
    OTP_MAX_ATTEMPTS,
    OTP_TTL_MINUTES,
    TokenType,
)
from app.models.otp_code import OtpCode

logger = logging.getLogger(__name__)


# ── Error ──────────────────────────────────────────────────────────────────────

class OtpError(Exception):
    """Raised by all OTP service functions. Router catches and maps to HTTP."""
    def __init__(self, message: str, status_code: int = status.HTTP_400_BAD_REQUEST):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _hash_code(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_code() -> str:
    """
    Cryptographically random numeric OTP.
    secrets.randbelow(10 ** N) is uniform over [0, 10^N) — no modulo bias.
    zfill preserves leading zeros (e.g. "042891").
    """
    return str(secrets.randbelow(10 ** OTP_LENGTH)).zfill(OTP_LENGTH)


def _make_email_verification_token(email: str) -> str:
    """
    Issue a short-lived email_verification_token after OTP success.

    Uses jwt.py's _create_token() directly because jwt.py's public
    create_access_token() only accepts (user_id, organization_id, branch_id,
    role, permissions) — it has no token_type, expires_delta, or extra_claims
    parameters. _create_token() is the correct internal surface for custom tokens.
    """
    from app.auth import jwt as _jwt
    return _jwt._create_token(
        subject=email,
        expires_delta=timedelta(minutes=OTP_TTL_MINUTES),
        token_type="access",
        extra_claims={
            "type": TokenType.EMAIL_VERIFICATION,
            "email": email,
        },
    )


# ── Email delivery ─────────────────────────────────────────────────────────────

async def _send_email_otp(email: str, code: str, purpose: str) -> None:
    """
    Send the OTP via SendGrid.

    Raises OtpError on delivery failure so the caller can roll back the
    OtpCode row — an undelivered code should leave no DB trace.
    """
    subject = (
        "Your verification code"
        if purpose == "verification"
        else "Your security code"
    )

    html_body = f"""
    <html>
      <body style="font-family:sans-serif;color:#333;max-width:480px;margin:auto;">
        <h2 style="margin-bottom:4px;">{subject}</h2>
        <p style="color:#555;">
          Enter this code to verify your identity.
          It expires in <strong>{OTP_TTL_MINUTES} minutes</strong>.
        </p>
        <div style="
          display:inline-block;padding:14px 28px;
          background:#f4f4f4;border-radius:8px;
          font-size:36px;font-weight:bold;letter-spacing:10px;
          margin:20px 0;
        ">{code}</div>
        <p style="color:#999;font-size:12px;">
          If you didn't request this, you can safely ignore this email.
        </p>
      </body>
    </html>
    """

    try:
        from app.core.email import send_email
        await send_email(to=email, subject=subject, html=html_body)
    except Exception:
        logger.error(
            "Failed to send OTP email",
            exc_info=True,
            extra={"recipient": email, "purpose": purpose},
        )
        raise OtpError(
            "Failed to send verification code. Please try again later.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )


# ── Public API ─────────────────────────────────────────────────────────────────

async def request_otp(
    session: AsyncSession,
    email: str,
    purpose: Literal["verification", "2fa_email", "2fa_sms"] = "verification",
) -> None:
    """
    Generate a new OTP, burn prior unused codes, store hash, deliver.
    Returns nothing — code is sent directly to the user.
    Raises OtpError on delivery failure (caller should rollback).

    For purpose="verification" (initial registration), rejects emails that
    already belong to an existing account — no code is generated or sent.
    For purpose="2fa_email", the user must already exist, so this guard
    is intentionally skipped.
    """
    email = email.lower().strip()

    # ── Guard: block re-registration attempts ──────────────────────────────────
    if purpose == "verification":
        from app.models import User
        existing_user = await session.execute(select(User).where(User.email == email))
        if existing_user.scalar_one_or_none():
            raise OtpError(
                "An account with this email already exists.",
                status.HTTP_409_CONFLICT,
            )

    existing = await session.execute(
        select(OtpCode).where(OtpCode.email == email, OtpCode.used == False)  # noqa: E712
    )
    for old in existing.scalars().all():
        old.used = True

    raw_code = _make_code()
    session.add(OtpCode(
        email=email,
        code_hash=_hash_code(raw_code),
        expires_at=_now() + timedelta(minutes=OTP_TTL_MINUTES),
        attempt_count=0,
        used=False,
    ))
    await session.flush()

    if purpose in ("verification", "2fa_email"):
        await _send_email_otp(email, raw_code, purpose)
    elif purpose == "2fa_sms":
        # TODO: integrate Twilio / AWS SNS
        raise OtpError(
            "SMS delivery is not currently configured. Please use email or TOTP.",
            status.HTTP_501_NOT_IMPLEMENTED,
        )


async def verify_otp(session: AsyncSession, email: str, code: str) -> str:
    """
    Verify a submitted OTP code.

    Attempt counting
    ─────────────────
    Every wrong guess increments attempt_count and flushes immediately.
    Once attempt_count reaches OTP_MAX_ATTEMPTS the record is locked
    (used=True) — forcing the user to request a fresh code.
    The router must commit() (not rollback) on OtpError so these
    increments survive. See app/core/guards.py for the commit logic.

    Returns
    ────────
    A short-lived email_verification JWT on success.
    """
    email = email.lower().strip()

    result = await session.execute(
        select(OtpCode)
        .where(
            OtpCode.email == email,
            OtpCode.used == False,  # noqa: E712
            OtpCode.expires_at > _now(),
        )
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()

    if record is None:
        raise OtpError(
            "Verification code has expired or was not requested. Please request a new one."
        )

    if record.attempt_count >= OTP_MAX_ATTEMPTS:
        raise OtpError(
            "Too many incorrect attempts. Please request a new code.",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if not hmac.compare_digest(record.code_hash, _hash_code(code.strip())):
        record.attempt_count += 1

        if record.attempt_count >= OTP_MAX_ATTEMPTS:
            record.used = True
            await session.flush()
            raise OtpError(
                "Too many incorrect attempts. Please request a new code.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )

        await session.flush()
        remaining = OTP_MAX_ATTEMPTS - record.attempt_count
        raise OtpError(
            f"Incorrect verification code. "
            f"{remaining} attempt{'s' if remaining != 1 else ''} remaining.",
        )

    record.used = True
    await session.flush()

    return _make_email_verification_token(email)