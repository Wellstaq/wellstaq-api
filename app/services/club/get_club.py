"""
app/services/club/get_club.py

Fetch a single club. Accessible to any org member — clubs are
discoverable org-wide regardless of branch.
List clubs — org-wide discovery or branch-scoped. Both list functions
accept requesting_user_id so each returned club can report whether the
requester is already a member — the frontend "Other Clubs" vs "My
Clubs" split depends entirely on this since there's no separate
membership list endpoint.
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club, ClubMember
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


async def _member_club_ids(session: AsyncSession, user_id: str, club_ids: list[str]) -> set[str]:
    """Given a list of club ids already fetched, returns the subset the
    user belongs to. One extra query, done once per list call — cheaper
    than a per-club membership check."""
    if not club_ids:
        return set()
    result = await session.execute(
        select(ClubMember.club_id).where(
            ClubMember.user_id == user_id,
            ClubMember.club_id.in_(club_ids),
        )
    )
    return set(result.scalars().all())


async def get_clubs(
    session: AsyncSession,
    organization_id: str,
    requesting_user_id: str,
    offset: int = 0,
    limit: int = 50,
    privacy: str | None = None,
) -> tuple[list[tuple[Club, bool]], int]:
    """List all clubs in the org (org-wide discovery). Returns
    (club, is_member) pairs so the frontend can split Other/My clubs
    without a second call."""
    query = select(Club).where(Club.organization_id == organization_id)

    if privacy is not None:
        query = query.where(Club.privacy == privacy)

    count_result = await session.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one()

    query = query.order_by(Club.name).offset(offset).limit(limit)
    result = await session.execute(query)
    clubs = list(result.scalars().all())

    member_ids = await _member_club_ids(session, requesting_user_id, [c.id for c in clubs])
    return [(c, c.id in member_ids) for c in clubs], total


async def get_branch_clubs(
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    requesting_user_id: str,
    offset: int = 0,
    limit: int = 50,
    privacy: str | None = None,
) -> tuple[list[tuple[Club, bool]], int]:
    """List clubs scoped to a specific branch. Same (club, is_member)
    shape as get_clubs."""
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
    clubs = list(result.scalars().all())

    member_ids = await _member_club_ids(session, requesting_user_id, [c.id for c in clubs])
    return [(c, c.id in member_ids) for c in clubs], total