"""
app/services/club/update_club.py

Partial update (PATCH) for a club.

branch_id is intentionally not updatable — a club cannot be moved
between branches after creation.
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club
from app.services.errors import AppError


async def edit_club(
    session: AsyncSession,
    organization_id: str,
    club_id: str,
    name: str | None,
    description: str | None,
    privacy: str | None,
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

    if name is not None:
        # Check name uniqueness within org (excluding self)
        existing = await session.execute(
            select(Club).where(
                Club.organization_id == organization_id,
                Club.name == name.strip(),
                Club.id != club_id,
            )
        )
        if existing.scalar_one_or_none():
            raise AppError(
                f"A club named '{name}' already exists in this organization.",
                status.HTTP_409_CONFLICT,
            )
        club.name = name.strip()

    if description is not None:
        club.description = description

    if privacy is not None:
        club.privacy = privacy

    await session.flush()
    return club