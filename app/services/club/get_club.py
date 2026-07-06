"""
app/services/club/get_club.py

Fetch a single club. Accessible to any org member — clubs are
discoverable org-wide regardless of branch.
List clubs — org-wide discovery or branch-scoped.
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club
from app.services.errors import AppError


async def get_club(
    session: AsyncSession,
    organization_id: str,
    club_id: str,
) -> Club:
    result = await session.execute(
        select(Club).where(
            Club.id == club_id,
            Club.organization_id == organization_id,
        )
    )
    club = result.scalar_one_or_none()
    if not club:
        raise AppError("Club not found.", status.HTTP_404_NOT_FOUND)
    return club 



async def get_clubs(
    session: AsyncSession,
    organization_id: str,
    offset: int = 0,
    limit: int = 50,
    privacy: str | None = None,
) -> tuple[list[Club], int]:
    """List all clubs in the org (org-wide discovery)."""
    query = select(Club).where(Club.organization_id == organization_id)

    if privacy is not None:
        query = query.where(Club.privacy == privacy)

    count_result = await session.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one()

    query = query.order_by(Club.name).offset(offset).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all()), total


async def get_branch_clubs(
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    offset: int = 0,
    limit: int = 50,
    privacy: str | None = None,
) -> tuple[list[Club], int]:
    """List clubs scoped to a specific branch."""
    query = select(Club).where(
        Club.organization_id == organization_id,
        Club.branch_id == branch_id,
    )

    if privacy is not None:
        query = query.where(Club.privacy == privacy)

    count_result = await session.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one()

    query = query.order_by(Club.name).offset(offset).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all()), total