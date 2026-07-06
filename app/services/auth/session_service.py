"""
app/services/auth/session_service.py

Session lifecycle:
  1. refresh_access_token — rotate refresh token, return new pair
  2. logout               — revoke refresh token for this device
  3. get_me               — load current user for /auth/me
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.jwt import TokenError, decode_token
from app.core.constants import TokenType, UserStatus
from app.models import User, UserDevice
from .token_service import hash_refresh_token, revoke_device, rotate_refresh_token
from ._helpers import AuthError

from app.models.department import DepartmentMember
from app.core.constants import RoleName


async def refresh_access_token(
    session: AsyncSession, raw_refresh_token: str
) -> dict:
    """
    Rotate the refresh token and return a new access + refresh pair.

    Each refresh token is single-use — rotate_refresh_token() overwrites
    the stored hash immediately, so a stolen token can only be replayed
    once before the legitimate user's next refresh invalidates it.

    Permissions are re-loaded from DB on every rotation so role/permission
    changes propagate without requiring a full re-login.
    """
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
            "Account not available. Please log in again.",
            status.HTTP_401_UNAUTHORIZED,
        )

    return await rotate_refresh_token(session, user, device)


async def logout(session: AsyncSession, raw_refresh_token: str) -> None:
    """
    Revoke the refresh token for this device.
    Silently succeeds if the token is already expired or unrecognised —
    idempotent so double-logout from a flaky client doesn't 4xx.
    """
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


async def get_me(session: AsyncSession, user_id: str, role: str | None = None) -> tuple[User, str | None]:
    """
    Load the current user with settings for /auth/me.
    Also resolves department_id unless the user is super_admin.
    Returns (user, department_id).
    """
    result = await session.execute(
        select(User)
        .options(selectinload(User.settings))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise AuthError("User not found.", status.HTTP_404_NOT_FOUND)

    # super_admin has no meaningful department — skip the lookup
    department_id: str | None = None
    if role != RoleName.SUPER_ADMIN:
        dept_result = await session.execute(
            select(DepartmentMember.department_id)
            .where(DepartmentMember.user_id == user_id)
        )
        department_id = dept_result.scalar_one_or_none()

    return user, department_id