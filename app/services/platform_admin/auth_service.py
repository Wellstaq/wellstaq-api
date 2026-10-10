"""
app/services/platform_admin/auth_service.py

Platform-admin authentication — login, 2FA challenge/verify, refresh,
logout, password forgot/reset. Deliberately parallel to, not shared
with, app/services/auth/* (org users): AdminUser is a separate table,
separate token scope ('admin'), separate session table (AdminSession
vs UserDevice). TOTP primitives are the one piece reused directly from
two_factor_service.py — they're pure functions with no User coupling.

Services never commit — the router owns the transaction boundary.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone

import bcrypt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import (
    TokenError,
    create_admin_access_token,
    create_admin_refresh_token,
    decode_token,
    hash_refresh_token,
)
from app.auth.security import hash_password, verify_password
from app.core.constants import ADMIN_PERMISSIONS, ADMIN_ROLE_PERMISSIONS, TokenType
from app.core.redis import get_redis
from app.models import AdminSession, AdminUser
from app.services.auth._helpers import _validate_password_strength
from app.services.auth.two_factor_service import verify_totp
from app.services.platform_admin.audit_service import log_admin_audit

logger = logging.getLogger(__name__)

PASSWORD_RESET_TOKEN_TTL_MINUTES = 30
PASSWORD_RESET_TOKEN_BYTES = 16

# A syntactically valid bcrypt hash with no real corresponding password —
# keeps verify_password's timing identical whether or not the email
# exists. Same anti-enumeration defense as login_service.py's _DUMMY_HASH.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt()).decode()


def _permissions_for(role: str) -> list[str]:
    if role == "super_admin":
        return list(ADMIN_PERMISSIONS)
    return list(ADMIN_ROLE_PERMISSIONS.get(role, ()))


async def _upsert_session(
    session: AsyncSession, admin: AdminUser, refresh_hash: str,
    device_name: str | None, ip_address: str | None,
) -> AdminSession:
    row = AdminSession(
        admin_user_id=admin.id, refresh_token_hash=refresh_hash,
        device_name=device_name, ip_address=ip_address,
        last_seen_at=datetime.now(timezone.utc), revoked=False,
    )
    session.add(row)
    await session.flush()
    return row


async def _issue_tokens(session: AsyncSession, admin: AdminUser, device_name: str | None, ip_address: str | None) -> dict:
    raw_refresh = create_admin_refresh_token(admin.id)
    refresh_hash = hash_refresh_token(raw_refresh)
    admin_session = await _upsert_session(session, admin, refresh_hash, device_name, ip_address)
    access_token = create_admin_access_token(
        admin_id=admin.id, role=admin.role, permissions=_permissions_for(admin.role), session_id=admin_session.id,
    )
    admin.last_login_at = datetime.now(timezone.utc)
    await session.flush()

    # Single choke point for a completed sign-in — called from both login()
    # (2FA off) and verify_2fa() (2FA on), so "signed in" is only logged
    # once a session is actually granted, not on a bare password check.
    await log_admin_audit(
        session=session, actor_admin_id=admin.id, action="admin.login_succeeded",
        target_type="admin", target_id=admin.id, target_label=admin.email, ip_address=ip_address,
    )

    return {"access_token": access_token, "refresh_token": raw_refresh, "token_type": "bearer", "expires_in": 900}


def _create_admin_2fa_challenge_token(admin_id: str, method: str) -> str:
    from datetime import timedelta
    from app.auth import jwt as _jwt_module
    return _jwt_module._create_token(
        subject=admin_id,
        expires_delta=timedelta(minutes=5),
        token_type="access",
        extra_claims={"type": TokenType.TWO_FA_CHALLENGE, "scope": "admin", "two_fa_method": method},
    )


async def login(
    *, session: AsyncSession, email: str, password: str, device_name: str | None = None, ip_address: str | None = None,
) -> dict:
    result = await session.execute(select(AdminUser).where(AdminUser.email == email.lower().strip()))
    admin = result.scalar_one_or_none()
    stored_hash = admin.hashed_password if admin and admin.hashed_password else _DUMMY_HASH
    password_ok = verify_password(password, stored_hash)

    if not password_ok or admin is None or not admin.hashed_password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    if admin.status == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This admin account has been disabled.")
    if admin.status == "invited":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Finish setting your password via the invite link first.")

    if admin.two_fa_enabled:
        if admin.two_fa_method == "email":
            from app.services.auth.otp_service import request_otp
            await request_otp(session, admin.email, purpose="admin_2fa_email")
        challenge_token = _create_admin_2fa_challenge_token(admin.id, admin.two_fa_method or "totp")
        return {"two_fa_required": True, "two_fa_challenge_token": challenge_token, "two_fa_method": admin.two_fa_method}

    logger.info("Admin logged in", extra={"admin_id": admin.id})
    return await _issue_tokens(session, admin, device_name, ip_address)


async def verify_2fa(
    *, session: AsyncSession, challenge_token: str, code: str, device_name: str | None = None, ip_address: str | None = None,
) -> dict:
    try:
        payload = decode_token(challenge_token)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This verification step has expired. Please log in again.") from exc
    if payload.get("type") != TokenType.TWO_FA_CHALLENGE or payload.get("scope") != "admin":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid challenge token.")

    admin_id = payload.get("sub")
    method = payload.get("two_fa_method")
    admin = (await session.execute(select(AdminUser).where(AdminUser.id == admin_id))).scalar_one_or_none()
    if admin is None or not admin.two_fa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA is not enabled on this account. Please log in again.")

    if method == "totp":
        if not admin.two_fa_secret or not verify_totp(admin.two_fa_secret, code):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired code.")
    elif method == "email":
        from app.services.auth.otp_service import OtpError, verify_otp
        try:
            await verify_otp(session, admin.email, code, purpose="admin_2fa_email")
        except OtpError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown 2FA method '{method}'.")

    logger.info("Admin 2FA verified", extra={"admin_id": admin.id})
    return await _issue_tokens(session, admin, device_name, ip_address)


async def refresh(*, session: AsyncSession, refresh_token: str) -> dict:
    try:
        payload = decode_token(refresh_token)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired.") from exc
    if payload.get("type") != TokenType.REFRESH or payload.get("scope") != "admin":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired.")

    admin_id = payload.get("sub")
    token_hash = hash_refresh_token(refresh_token)
    result = await session.execute(
        select(AdminSession).where(
            AdminSession.admin_user_id == admin_id,
            AdminSession.refresh_token_hash == token_hash,
            AdminSession.revoked.is_(False),
        )
    )
    admin_session = result.scalar_one_or_none()
    if admin_session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired.")

    admin = (await session.execute(select(AdminUser).where(AdminUser.id == admin_id))).scalar_one()

    new_raw_refresh = create_admin_refresh_token(admin.id)
    # Rotation: a leaked refresh token is single-use — overwrite the hash
    # so the superseded raw token is unrecognisable on its next use.
    admin_session.refresh_token_hash = hash_refresh_token(new_raw_refresh)
    admin_session.last_seen_at = datetime.now(timezone.utc)
    new_access = create_admin_access_token(
        admin_id=admin.id, role=admin.role, permissions=_permissions_for(admin.role), session_id=admin_session.id,
    )
    await session.flush()
    return {"access_token": new_access, "refresh_token": new_raw_refresh, "token_type": "bearer", "expires_in": 900}


async def logout(*, session: AsyncSession, session_id: str) -> None:
    result = await session.execute(select(AdminSession).where(AdminSession.id == session_id))
    admin_session = result.scalar_one_or_none()
    if admin_session is None:
        return
    admin_session.revoked = True
    admin_session.refresh_token_hash = None
    await session.flush()


def _reset_token_key(*, reset_token: str) -> str:
    return f"admin_password_reset:{hashlib.sha256(reset_token.encode()).hexdigest()}"


def _used_token_key(*, reset_token: str) -> str:
    return f"admin_used_reset_token:{hashlib.sha256(reset_token.encode()).hexdigest()}"


async def _send_reset_email(*, email: str, reset_token: str) -> None:
    from app.core.config import settings
    from app.core.email import send_email
    reset_url = f"{settings.ADMIN_APP_ORIGIN.rstrip('/')}/reset-password?token={reset_token}"
    html_body = f"""
    <html><body style="font-family:sans-serif;color:#333;max-width:480px;margin:auto;">
      <h2>Reset your Wellstaq admin password</h2>
      <p style="color:#555;">This link expires in {PASSWORD_RESET_TOKEN_TTL_MINUTES} minutes.</p>
      <p><a href="{reset_url}">Reset password</a></p>
      <p style="color:#999;font-size:12px;">If you didn't request this, you can safely ignore this email.</p>
    </body></html>
    """
    try:
        await send_email(to=email, subject="Reset your Wellstaq admin password", html=html_body)
    except Exception:
        logger.error("Failed to send admin password reset email", exc_info=True, extra={"recipient": email})


async def request_password_reset(*, session: AsyncSession, email: str) -> None:
    """Deliberately a no-op (not an error) when the email doesn't match
    an admin — prevents enumerating which emails are platform staff."""
    email = email.lower().strip()
    admin = (await session.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()
    if admin is None:
        logger.info("Admin password reset requested for unknown email — no-op", extra={"email": email})
        return

    reset_token = secrets.token_urlsafe(PASSWORD_RESET_TOKEN_BYTES)
    redis = await get_redis()
    await redis.set(_reset_token_key(reset_token=reset_token), email, ex=PASSWORD_RESET_TOKEN_TTL_MINUTES * 60)
    await _send_reset_email(email=email, reset_token=reset_token)
    logger.info("Admin password reset email sent", extra={"admin_id": admin.id})


async def reset_password(*, session: AsyncSession, reset_token: str, new_password: str) -> None:
    """Also used to set an invited admin's FIRST password — the invite
    email reuses this same reset-link mechanism (see admins.invite in
    admin_team_service.py) rather than a separate accept-invite endpoint,
    since the admin console's own API client has no such call."""
    redis = await get_redis()
    token_key = _reset_token_key(reset_token=reset_token)
    email_value = await redis.get(token_key)
    if email_value is None:
        if await redis.get(_used_token_key(reset_token=reset_token)) is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link has already been used.")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset link.")

    email = (email_value.decode() if isinstance(email_value, bytes) else str(email_value)).lower().strip()
    admin = (await session.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()
    if admin is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset link.")

    _validate_password_strength(new_password)
    admin.hashed_password = hash_password(new_password)
    if admin.status == "invited":
        admin.status = "active"
    await session.flush()

    remaining_seconds = await redis.ttl(token_key)
    if remaining_seconds > 0:
        await redis.set(_used_token_key(reset_token=reset_token), "1", ex=remaining_seconds)
    await redis.delete(token_key)
    logger.info("Admin password reset completed", extra={"admin_id": admin.id})
