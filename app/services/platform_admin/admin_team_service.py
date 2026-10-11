"""
app/services/platform_admin/admin_team_service.py

Managing the AdminUser roster itself — list/invite/role-change/remove.
Guard rails per docs/ADMIN_API_CONTRACT.md section 11 on the admin
console repo: an admin can't change their own role or remove
themselves, and the last super_admin can't be demoted or removed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ADMIN_ROLES
from app.models import AdminUser


async def list_admins(*, session: AsyncSession, q: str | None, offset: int, limit: int) -> tuple[list[AdminUser], int]:
    # Removing an admin is a soft delete (status="disabled") so the audit
    # log keeps attribution for their past actions — see remove_admin.
    # Exclude them from the roster by default so a removed admin or a
    # cancelled invite doesn't linger in the team list after reload.
    query = select(AdminUser).where(AdminUser.status != "disabled")
    count_query = select(func.count()).select_from(AdminUser).where(AdminUser.status != "disabled")
    if q:
        pattern = f"%{q}%"
        from sqlalchemy import or_
        clause = or_(AdminUser.first_name.ilike(pattern), AdminUser.last_name.ilike(pattern), AdminUser.email.ilike(pattern))
        query, count_query = query.where(clause), count_query.where(clause)
    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.order_by(AdminUser.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return list(rows), total


async def _count_active_super_admins(session: AsyncSession, exclude_id: str | None = None) -> int:
    query = select(func.count()).select_from(AdminUser).where(AdminUser.role == "super_admin", AdminUser.status != "disabled")
    if exclude_id:
        query = query.where(AdminUser.id != exclude_id)
    return (await session.execute(query)).scalar_one()


async def invite_admin(*, session: AsyncSession, email: str, role: str, invited_by: str) -> AdminUser:
    if role not in ADMIN_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid role '{role}'.")

    email = email.lower().strip()
    existing = (await session.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()
    if existing is not None and existing.status != "disabled":
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is already an admin.")

    invited_at = datetime.now(timezone.utc)
    if existing is not None:
        # A disabled row (removed admin, a cancelled invite, or one swept
        # by expire_admin_invites after INVITE_TTL_DAYS — see
        # app/jobs/platform_admin/expire_admin_invites.py) is excluded
        # from the roster — see list_admins' docstring — so from the
        # inviting admin's point of view that email looks free. Re-inviting
        # it should work, not 409 on a row they can't even see. Resurrect
        # the row rather than inserting a new one: AdminUser.email is
        # unique, and this keeps the audit log's actor_admin_id references
        # to the old AdminUser.id intact.
        admin = existing
        admin.role = role
        admin.status = "invited"
        admin.invited_by = invited_by
        admin.invited_at = invited_at
        admin.hashed_password = None
        admin.first_name = None
        admin.last_name = None
        admin.avatar_url = None
        admin.two_fa_enabled = False
        admin.two_fa_method = None
        admin.two_fa_secret = None
        admin.last_login_at = None
    else:
        admin = AdminUser(email=email, role=role, status="invited", invited_by=invited_by, invited_at=invited_at)
        session.add(admin)

    await session.flush()
    await session.refresh(admin)

    # Reuses the password-reset link mechanism to let the invited admin
    # set their first password — see auth_service.reset_password's docstring.
    from app.services.platform_admin.auth_service import request_password_reset
    await request_password_reset(session=session, email=email)

    return admin


async def update_admin_role(*, session: AsyncSession, admin_id: str, new_role: str, acting_admin_id: str) -> AdminUser:
    if new_role not in ADMIN_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid role '{new_role}'.")
    if admin_id == acting_admin_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role.")

    admin = (await session.execute(select(AdminUser).where(AdminUser.id == admin_id))).scalar_one_or_none()
    if admin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin not found.")

    if admin.role == "super_admin" and new_role != "super_admin":
        if await _count_active_super_admins(session, exclude_id=admin_id) == 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "The last super admin cannot be demoted.")

    admin.role = new_role
    await session.flush()
    await session.refresh(admin)
    return admin


async def remove_admin(*, session: AsyncSession, admin_id: str, acting_admin_id: str) -> None:
    if admin_id == acting_admin_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove yourself.")

    admin = (await session.execute(select(AdminUser).where(AdminUser.id == admin_id))).scalar_one_or_none()
    if admin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin not found.")

    if admin.role == "super_admin" and await _count_active_super_admins(session, exclude_id=admin_id) == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "The last super admin cannot be removed.")

    from app.models import AdminSession as AdminSessionModel
    await session.execute(
        AdminSessionModel.__table__.update().where(AdminSessionModel.admin_user_id == admin_id).values(revoked=True, refresh_token_hash=None)
    )
    admin.status = "disabled"
    await session.flush()
