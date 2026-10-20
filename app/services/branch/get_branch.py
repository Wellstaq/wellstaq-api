"""
app/services/branch/get_branch.py

Read operations for branches — single fetch, list, and members.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, User

logger = logging.getLogger(__name__)


async def get_branch(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
) -> Branch:
    """Fetch a single branch, scoped to the organization."""
    result = await session.execute(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == organization_id,
        )
    )
    branch = result.scalar_one_or_none()
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found or does not belong to this organization.",
        )
    return branch


async def get_branches(
    *,
    session: AsyncSession,
    organization_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Branch], int]:
    """List all branches in an organization, paginated."""
    count_result = await session.execute(
        select(func.count())
        .select_from(Branch)
        .where(Branch.organization_id == organization_id)
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(Branch)
        .where(Branch.organization_id == organization_id)
        .order_by(Branch.name)
        .offset(offset)
        .limit(limit)
    )
    branches = result.scalars().all()
    return branches, total


async def get_branch_members(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[User], int]:
    """
    Return users assigned to a branch, plus a total count.

    Branch membership is a direct FK (User.branch_id) — not a join table
    like DepartmentMember — so this is a straight filtered query rather
    than a join.

    Raises:
      404 — branch not found or does not belong to this organization.
    """
    # ── Validate branch belongs to this org ─────────────────────────────────
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
        .select_from(User)
        .where(User.branch_id == branch_id, User.organization_id == organization_id, User.email.notlike("%@deleted.wellstaq.invalid"))
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(User)
        .where(User.branch_id == branch_id, User.organization_id == organization_id, User.email.notlike("%@deleted.wellstaq.invalid"))
        .order_by(User.first_name, User.last_name)
        .offset(offset)
        .limit(limit)
    )
    members = result.scalars().all()
    return members, total