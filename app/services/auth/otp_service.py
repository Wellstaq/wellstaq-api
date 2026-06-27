"""
app/services/otp_service.py

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

from app.auth.jwt import create_access_token
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
    zfill ensures leading zeros are preserved (e.g. "042891").
    """
    return str(secrets.randbelow(10 ** OTP_LENGTH)).zfill(OTP_LENGTH)


# ── Email delivery ─────────────────────────────────────────────────────────────

async def _send_email_otp(email: str, code: str, purpose: str) -> None:
    """
    Send the OTP to the user's email address.

    Raises OtpError on delivery failure so the caller can roll back the
    OtpCode row — an undelivered code should leave no DB trace.

    The original exception is logged with exc_info=True before re-raising
    so the full traceback is visible in log aggregators. The raw exception
    is never forwarded to the client.
    """
    try:
        from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
        from app.core.config import settings as app_settings

        conf = ConnectionConfig(
            MAIL_USERNAME=app_settings.MAIL_USERNAME,
            MAIL_PASSWORD=app_settings.MAIL_PASSWORD,
            MAIL_FROM=app_settings.MAIL_FROM,
            MAIL_PORT=app_settings.MAIL_PORT,
            MAIL_SERVER=app_settings.MAIL_SERVER,
            MAIL_STARTTLS=app_settings.MAIL_STARTTLS,
            MAIL_SSL_TLS=app_settings.MAIL_SSL_TLS,
            USE_CREDENTIALS=True,
            VALIDATE_CERTS=True,
        )

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

        message = MessageSchema(
            subject=subject,
            recipients=[email],
            body=html_body,
            subtype=MessageType.html,
        )
        await FastMail(conf).send_message(message)

    except Exception:
        # Log the real cause with full traceback before replacing it with a
        # safe user-facing message. Without this, email delivery outages are
        # completely invisible in production logs.
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
    Generate a new OTP, burn any prior unused codes, store the hash, and deliver.

    Returns nothing — the code is sent directly to the user's device.
    Raises OtpError if delivery fails (caller should rollback so the
    undelivered OtpCode row does not survive).
    """
    email = email.lower().strip()

    # Burn existing unused codes for this email — one live code at a time.
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
    (used=True) before raising — forcing the user to request a fresh code.
    This flush MUST reach the DB even when OtpError is raised, so the
    router must call db.commit() (not rollback) on OtpError.
    See app/routers/auth.py and app/core/guards.py for the commit logic.

    Returns
    ────────
    A short-lived email_verification JWT on success.
    The token type is TokenType.EMAIL_VERIFICATION so auth_service can
    distinguish it from a regular access token.

    Raises
    ───────
    OtpError — wrong code, too many attempts, expired, or not found.
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

    # Check lockout before hash comparison — avoids leaking timing info
    # about whether a locked record exists.
    if record.attempt_count >= OTP_MAX_ATTEMPTS:
        # Already locked — should not normally be reached because we set
        # used=True when locking, but guard here as defence in depth.
        raise OtpError(
            "Too many incorrect attempts. Please request a new code.",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if not hmac.compare_digest(record.code_hash, _hash_code(code.strip())):
        record.attempt_count += 1

        if record.attempt_count >= OTP_MAX_ATTEMPTS:
            # Lock the record so it cannot be retried even if not yet expired.
            record.used = True
            await session.flush()
            raise OtpError(
                "Too many incorrect attempts. Please request a new code.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )

        await session.flush()
        remaining = OTP_MAX_ATTEMPTS - record.attempt_count
        raise OtpError(
            f"Incorrect verification code. {remaining} attempt{'s' if remaining != 1 else ''} remaining.",
        )

    # Correct code — mark used and issue verification token.
    record.used = True
    await session.flush()

    return create_access_token(
        user_id=email,                          # sub claim carries the email here
        organization_id=None,
        branch_id=None,
        role=None,
        permissions=[],
        token_type=TokenType.EMAIL_VERIFICATION,
        expires_delta=timedelta(minutes=OTP_TTL_MINUTES),
        extra_claims={"email": email},
    )