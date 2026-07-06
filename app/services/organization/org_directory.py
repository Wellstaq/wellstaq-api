"""
app/services/organization/org_directory.py

Read-only "organization directory" queries — members, clubs, and
(for convenience) branches/departments scoped to an org.

Branches and departments already have first-class list endpoints
(GET /organizations/{org_id}/branches, GET /organizations/{org_id}/departments)
in their own routers 
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Club, Organization, User

logger = logging.getLogger(__name__)


async def _require_org(session: AsyncSession, organization_id: str) -> Organization:
    result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )
    return org


async def get_organization_members(
    *,
    session: AsyncSession,
    organization_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[User], int]:
    """All users belonging to the organization, regardless of branch/department."""
    await _require_org(session, organization_id)

    count_result = await session.execute(
        select(func.count()).select_from(User).where(User.organization_id == organization_id)
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(User)
        .where(User.organization_id == organization_id)
        .order_by(User.first_name, User.last_name)
        .offset(offset)
        .limit(limit)
    )
    members = result.scalars().all()
    return members, total


async def get_organization_clubs(
    *,
    session: AsyncSession,
    organization_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Club], int]:
    """All clubs belonging to the organization, across every branch."""
    await _require_org(session, organization_id)

    count_result = await session.execute(
        select(func.count()).select_from(Club).where(Club.organization_id == organization_id)
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(Club)
        .where(Club.organization_id == organization_id)
        .order_by(Club.name)
        .offset(offset)
        .limit(limit)
    )
    clubs = result.scalars().all()
    return clubs, total

