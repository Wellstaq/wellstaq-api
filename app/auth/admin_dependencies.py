"""
app/auth/admin_dependencies.py

FastAPI dependencies protecting /app-admin/* routes — mirrors
app/auth/dependencies.py's get_current_user / require_permission, but
for the wholly separate AdminUser identity. An org access token is
rejected here (no scope='admin' claim); an admin access token is
rejected by get_current_user for the same reason, in both directions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TokenError, decode_token
from app.core.constants import ADMIN_PERMISSIONS, ADMIN_ROLE_PERMISSIONS, TokenType
from app.core.database import get_db
from app.models import AdminSession, AdminUser

admin_http_bearer = HTTPBearer()


@dataclass(frozen=True)
class CurrentAdmin:
    admin_id: str
    role: str
    permissions: list[str] = field(default_factory=list)
    session_id: str | None = None

    def has_permission(self, permission: str) -> bool:
        return self.role == "super_admin" or permission in self.permissions


async def get_current_admin(
    credentials=Depends(admin_http_bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentAdmin:
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate admin credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
    except TokenError as exc:
        raise credentials_exception from exc

    if payload.get("type") != TokenType.ACCESS or payload.get("scope") != "admin":
        raise credentials_exception

    admin_id: str | None = payload.get("sub")
    session_id: str | None = payload.get("sid")
    if not admin_id or not session_id:
        raise credentials_exception

    active_session = (await db.execute(
        select(AdminSession.id).where(
            AdminSession.id == session_id,
            AdminSession.admin_user_id == admin_id,
            AdminSession.revoked.is_(False),
        )
    )).scalar_one_or_none()
    if active_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This admin session has been revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Loaded fresh from DB (not trusted from the token) so a role change
    # or disable takes effect immediately, same reasoning as get_current_user.
    admin = (await db.execute(select(AdminUser).where(AdminUser.id == admin_id))).scalar_one_or_none()
    if admin is None:
        raise credentials_exception
    if admin.status != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This admin account is not active.")

    # super_admin isn't a key in ADMIN_ROLE_PERMISSIONS — it implicitly
    # holds every permission (see that dict's own docstring in constants.py).
    permissions = list(ADMIN_PERMISSIONS) if admin.role == "super_admin" else list(ADMIN_ROLE_PERMISSIONS.get(admin.role, ()))
    return CurrentAdmin(admin_id=admin.id, role=admin.role, permissions=permissions, session_id=session_id)


def require_admin_permission(permission: str):
    """Route-level dependency — 403s unless the admin's role grants this
    permission. super_admin implicitly passes every check (see
    CurrentAdmin.has_permission)."""

    async def _check(current_admin: CurrentAdmin = Depends(get_current_admin)) -> CurrentAdmin:
        if not current_admin.has_permission(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing required permission: {permission}.")
        return current_admin

    return _check
