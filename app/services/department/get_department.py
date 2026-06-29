"""
app/services/department/get_department.py

Read operations for departments.

get_departments  — list all departments in an org (paginated)
get_department   — fetch a single department by ID, with membership check
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department, DepartmentMember

logger = logging.getLogger(__name__)


async def get_departments(
    *,
    session: AsyncSession,
    organization_id: str,
    offset: int = 0,
    limit: int = 50,
) -> list[Department]:
    """
    Return all departments belonging to an org, ordered by name.
    Paginated via offset/limit.
    """
    result = await session.execute(
        select(Department)
        .where(Department.organization_id == organization_id)
        .order_by(Department.name)
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_department(
    *,
    session: AsyncSession,
    organization_id: str,
    department_id: str,
    user_id: str,
) -> Department:
    """
    Fetch a single department by ID.

    Access rules:
      - User holds department.create org-wide → always allowed.
      - Otherwise → user must be a member of the department.

    Raises:
      404 — department not found or belongs to a different org.
      403 — user has no permission and is not a member.
    """
    result = await session.execute(
        select(Department).where(
            Department.id == department_id,
            Department.organization_id == organization_id,
        )
    )
    department = result.scalar_one_or_none()
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Department not found.",
        )

    
    return department

    # Check membership
    membership = await session.execute(
        select(DepartmentMember).where(
            DepartmentMember.department_id == department_id,
            DepartmentMember.user_id == user_id,
        )
    )
    if membership.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view this department.",
        )

    return department