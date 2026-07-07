"""
app/services/department/get_branch_departments.py

Fetch departments belonging to a specific branch, paginated. Mirrors
get_branch_clubs() (app/services/club.py) structurally — same
branch-existence guard, same (items, total) return shape.

Department.branch_id is NOT NULL (see department.py model docstring) —
every department belongs to exactly one branch, so this is a plain
filtered list; there's no separate "org-level" bucket to reconcile.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch
from app.models.department import Department


async def get_branch_departments(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Department], int]:
    """
    List departments under a branch.

    Returns (departments, total_count) — total_count is the full count
    for this branch regardless of offset/limit, matching the pagination
    shape used by get_departments()/get_branch_clubs().

    Raises:
      404 — branch not found or does not belong to this organization.
    """
    branch_result = await session.execute(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == organization_id,
        )
    )
    if branch_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found or does not belong to this organization.",
        )

    count_result = await session.execute(
        select(func.count())
        .select_from(Department)
        .where(
            Department.branch_id == branch_id,
            Department.organization_id == organization_id,
        )
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(Department)
        .where(
            Department.branch_id == branch_id,
            Department.organization_id == organization_id,
        )
        .order_by(Department.name)
        .offset(offset)
        .limit(limit)
    )
    departments = list(result.scalars().all())

    return departments, total