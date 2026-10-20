"""
app/services/department/department_member.py

Business logic for department operations.

Contract:
  - All functions accept a live AsyncSession.
  - None of them call commit() — the router owns that.
  - flush() is called after INSERT to populate the PK before the router
    serializes the response.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department, User
from app.models.department import DepartmentMember

logger = logging.getLogger(__name__)


async def get_department_members(
    *,
    session: AsyncSession,
    organization_id: str,
    department_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[User], int]:
    """
    Return the users belonging to a department, plus a total count.

    Validates the department belongs to this org first — this both
    prevents cross-tenant enumeration (guessing a dept_id from another
    org) and gives a clean 404 instead of an empty list when the
    department simply doesn't exist for this caller.

    Returns:
      (members, total) — members is the paginated page of Users,
      total is the full membership count (for client-side pagination UI).
    """
    # ── Validate department belongs to this org ────────────────────────────────
    dept_result = await session.execute(
        select(Department).where(
            Department.id == department_id,
            Department.organization_id == organization_id,
        )
    )
    department = dept_result.scalar_one_or_none()
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found or does not belong to this organization.",
        )

    # ── Total count (for pagination metadata) ───────────────────────────────────
    count_result = await session.execute(
        select(func.count())
        .select_from(DepartmentMember)
        .join(User, User.id == DepartmentMember.user_id)
        .where(DepartmentMember.department_id == department_id)
        .where(User.email.notlike("%@deleted.wellstaq.invalid"))
    )
    total = count_result.scalar_one()

    # ── Paginated members, joined to User ───────────────────────────────────────
    # organization_id is re-checked on User here too — belt-and-suspenders
    # in case a membership row ever ends up pointing at a stale/cross-org
    # user_id (shouldn't happen given assign_user_to_department's checks,
    # but this keeps the read path independently safe).
    result = await session.execute(
        select(User)
        .join(DepartmentMember, DepartmentMember.user_id == User.id)
        .where(
            DepartmentMember.department_id == department_id,
            User.organization_id == organization_id,
            User.email.notlike("%@deleted.wellstaq.invalid"),
        )
        .order_by(User.first_name, User.last_name)
        .offset(offset)
        .limit(limit)
    )
    members = result.scalars().all()

    return members, total