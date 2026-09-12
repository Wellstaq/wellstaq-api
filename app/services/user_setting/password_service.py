"""
app/services/user_settings/password_service.py

change_password: for a logged-in user who knows their current password.
forgot_password / reset_password: for a logged-out user who doesn't —
uses a short-lived, opaque reset token sent via email, never an OTP (a reset link is
the more standard UX for "I forgot my password" than a 6-digit code,
though OTP could work too — this follows the more common convention).
"""

from __future__ import annotations

import logging
import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TokenError, decode_token
from app.auth.security import hash_password, verify_password
from app.core.constants import TokenType
from app.models import User
from app.services.auth._helpers import _validate_password_strength
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

PASSWORD_RESET_TOKEN_TTL_MINUTES = 30
PASSWORD_RESET_TOKEN_BYTES = 16


def _used_token_key(*, reset_token: str) -> str:
    # Hash the token rather than storing it raw as a key — avoids
    # putting a sensitive credential-equivalent string directly into
    # Redis key names (which can show up in logs/monitoring tools).
    return f"used_reset_token:{hashlib.sha256(reset_token.encode()).hexdigest()}"


def _reset_token_key(*, reset_token: str) -> str:
    """Redis key for the short-lived opaque token's email binding."""
    return f"password_reset:{hashlib.sha256(reset_token.encode()).hexdigest()}"


async def change_password(*, session: AsyncSession, user_id: str, current_password: str, new_password: str) -> None:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")

    _validate_password_strength(new_password)

    user.hashed_password = hash_password(new_password)
    await session.flush()
    logger.info("Password changed", extra={"user_id": user_id})


async def request_password_reset(*, session: AsyncSession, email: str) -> None:
    """
    Deliberately does NOT reveal whether the email exists — always
    returns success/no-op from the caller's perspective, whether or not
    an account was found. Prevents email enumeration via this endpoint.
    """
    email = email.lower().strip()
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        logger.info("Password reset requested for unknown email — no-op", extra={"email": email})
        return  # silent no-op, same response as success to the caller

    # A 16-byte URL-safe token is 128 bits of entropy (about 22 characters),
    # substantially shorter than a JWT while remaining infeasible to guess.
    reset_token = secrets.token_urlsafe(PASSWORD_RESET_TOKEN_BYTES)
    redis = await get_redis()
    await redis.set(
        _reset_token_key(reset_token=reset_token),
        email,
        ex=PASSWORD_RESET_TOKEN_TTL_MINUTES * 60,
    )

    await _send_reset_email(email=email, reset_token=reset_token)
    logger.info("Password reset email sent", extra={"user_id": user.id})




async def reset_password(*, session: AsyncSession, reset_token: str, new_password: str) -> None:
    redis = await get_redis()
    token_key = _reset_token_key(reset_token=reset_token)
    email_value = await redis.get(token_key)
    used_key = _used_token_key(reset_token=reset_token)
    remaining_seconds: int | None = None
    if email_value is None:
        if await redis.get(used_key) is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link has already been used.")

        # Accept reset JWTs issued just before the opaque-token rollout so
        # users do not lose a still-valid link during deployment.
        try:
            payload = decode_token(reset_token)
        except TokenError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset link.") from exc
        if payload.get("type") != TokenType.PASSWORD_RESET:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset link.")
        email_value = payload.get("email", "")
        remaining_seconds = int(payload.get("exp", 0)) - int(datetime.now(timezone.utc).timestamp())

    if isinstance(email_value, bytes):
        email_value = email_value.decode()
    email = str(email_value).lower().strip()
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset link.")

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset link.")

    _validate_password_strength(new_password)

    user.hashed_password = hash_password(new_password)
    await session.flush()

    # Mark used AFTER the password change succeeds — if hash_password or
    # the flush somehow failed, we want the token to remain usable for a
    # retry, not burned on a failed attempt.
    if remaining_seconds is None:
        remaining_seconds = await redis.ttl(token_key)
    if remaining_seconds > 0:
        await redis.set(used_key, "1", ex=remaining_seconds)
    await redis.delete(token_key)

    logger.info("Password reset completed", extra={"user_id": user.id})

async def _send_reset_email(*, email: str, reset_token: str) -> None:
    reset_url = f"https://appwellstaq6.vercel.app/reset-password?token={reset_token}"

    html_body = f"""
    <html>
      <body style="font-family:sans-serif;color:#333;max-width:480px;margin:auto;">
        <h2>Reset your password</h2>
        <p style="color:#555;">
          Click the link below to reset your password. This link expires
          in {PASSWORD_RESET_TOKEN_TTL_MINUTES} minutes.
        </p>
        <a href="{reset_url}" style="
          display:inline-block;padding:12px 28px;
          background:#4F46E5;color:#fff;border-radius:8px;
          font-weight:600;text-decoration:none;margin:20px 0;
        ">Reset Password</a>
        <p style="color:#555;font-size:13px;margin:4px 0 0;">
          You can also copy this link manually to your browser:
        </p>
        <p style="color:#777;font-size:12px;word-break:break-all;margin-top:6px;">
          <a href="{reset_url}" style="color:#4F46E5;">{reset_url}</a>
        </p>
        <p style="color:#999;font-size:12px;">
          If you didn't request this, you can safely ignore this email.
        </p>
      </body>
    </html>
    """

    from app.core.email import send_email
    await send_email(to=email, subject="Reset your Wellstaq password", html=html_body)
