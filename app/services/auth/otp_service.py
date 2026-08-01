"""
app/services/auth/otp_service.py

Centralised OTP management — now backed by Redis instead of an OtpCode
DB table. OTPs are inherently short-lived and high-write; Redis's
native key expiry replaces expires_at + the "burn prior codes" step
(setting a new key for the same email+purpose naturally supersedes the
old one), and needs no periodic purge job the way a DB table would.

Storage shape: one Redis key per (email, purpose), holding a JSON blob
{code_hash, attempt_count} — preserves the exact same attempt-counting
and hashing behavior as the original DB-backed version, just stored
differently. TTL on the key IS the expiry — no expires_at field needed.

session parameter removed from both functions — nothing here touches
the DB anymore except the purpose="verification" duplicate-email guard,
which still needs a session since it queries the User table.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from typing import Literal

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    OTP_LENGTH,
    OTP_MAX_ATTEMPTS,
    OTP_TTL_MINUTES,
    EMAIL_VERIFICATION_TOKEN_TTL_DAYS,
    TokenType,
)
from app.core.redis import get_redis
from app.services.errors import OtpError

logger = logging.getLogger(__name__)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _hash_code(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _make_code() -> str:
    return str(secrets.randbelow(10 ** OTP_LENGTH)).zfill(OTP_LENGTH)


def _otp_key(*, email: str, purpose: str) -> str:
    return f"otp:{purpose}:{email}"


def _make_email_verification_token(email: str) -> str:
    from datetime import timedelta
    from app.auth import jwt as _jwt
    return _jwt._create_token(
        subject=email,
        expires_delta=timedelta(days=EMAIL_VERIFICATION_TOKEN_TTL_DAYS),
        token_type="access",
        extra_claims={"type": TokenType.EMAIL_VERIFICATION, "email": email},
    )


# ── Email delivery ─────────────────────────────────────────────────────────────

async def _send_email_otp(email: str, code: str, purpose: str) -> None:
    subject = "Your verification code" if purpose == "verification" else "Your security code"

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
        logger.error("Failed to send OTP email", exc_info=True, extra={"recipient": email, "purpose": purpose})
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
    session is still required — the purpose="verification" duplicate-
    email guard queries the User table, which still lives in the DB.
    Nothing else in this function touches the DB anymore.
    """
    email = email.lower().strip()

    if purpose == "verification":
        from app.models import User
        existing_user = await session.execute(select(User).where(User.email == email))
        if existing_user.scalar_one_or_none():
            raise OtpError("An account with this email already exists.", status.HTTP_409_CONFLICT)

    if purpose == "2fa_sms":
        raise OtpError(
            "SMS delivery is not currently configured. Please use email or TOTP.",
            status.HTTP_501_NOT_IMPLEMENTED,
        )

    raw_code = _make_code()
    payload = json.dumps({"code_hash": _hash_code(raw_code), "attempt_count": 0})

    redis = await get_redis()
    # Setting the key overwrites any prior unused code for this
    # email+purpose automatically — this IS the "burn prior codes" step,
    # no separate query/update needed like the DB version required.
    await redis.set(_otp_key(email=email, purpose=purpose), payload, ex=OTP_TTL_MINUTES * 60)

    await _send_email_otp(email, raw_code, purpose)


async def verify_otp(session: AsyncSession, email: str, code: str, purpose: str = "verification") -> str:
    """
    purpose is now a required lookup key (Redis needs to know which key
    to check) — the original DB version could find "the latest unused
    code for this email" without knowing purpose ahead of time, since it
    just queried by email + used=False. Redis needs the exact key, so
    callers must now pass purpose explicitly. Check call sites of
    verify_otp() for this — anywhere that doesn't currently pass
    purpose will need updating to pass whichever purpose that flow uses
    (almost certainly "verification" for registration flows, matching
    the default).
    """
    email = email.lower().strip()
    redis = await get_redis()
    key = _otp_key(email=email, purpose=purpose)

    raw = await redis.get(key)
    if raw is None:
        raise OtpError("Verification code has expired or was not requested. Please request a new one.")

    record = json.loads(raw)

    if record["attempt_count"] >= OTP_MAX_ATTEMPTS:
        await redis.delete(key)
        raise OtpError("Too many incorrect attempts. Please request a new code.", status.HTTP_429_TOO_MANY_REQUESTS)

    if not hmac.compare_digest(record["code_hash"], _hash_code(code.strip())):
        record["attempt_count"] += 1

        if record["attempt_count"] >= OTP_MAX_ATTEMPTS:
            await redis.delete(key)
            raise OtpError("Too many incorrect attempts. Please request a new code.", status.HTTP_429_TOO_MANY_REQUESTS)

        # TTL must be preserved on this update — a plain SET without EX
        # would make the key persist forever. Use KEEPTTL to update the
        # value while leaving the existing expiry untouched.
        await redis.set(key, json.dumps(record), keepttl=True)

        remaining = OTP_MAX_ATTEMPTS - record["attempt_count"]
        raise OtpError(f"Incorrect verification code. {remaining} attempt{'s' if remaining != 1 else ''} remaining.")

    # Success — delete immediately, one-time use.
    await redis.delete(key)

    return _make_email_verification_token(email)