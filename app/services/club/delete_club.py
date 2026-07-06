"""
app/services/club/delete_club.py

Delete a club.

All ClubMember rows are cascade-deleted by the DB (ondelete=CASCADE
on ClubMember.club_id). Member users are not deleted — they just lose
their club membership and are left without a club, which is acceptable
(clubs are optional affinity groups, not required like departments).
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club
from app.services.errors import AppError


async def delete_club(
    session: AsyncSession,
    organization_id: str,
    club_id: str,
) -> dict:
    result = await session.execute(
        select(Club).where(
            Club.id == club_id,
            Club.organization_id == organization_id,
        )
    )
    club = result.scalar_one_or_none()
    if not club:
        raise AppError("Club not found.", status.HTTP_404_NOT_FOUND)

    await session.delete(club)
    await session.flush()

    return {
        "club_id": club_id,
        "organization_id": organization_id,
    }