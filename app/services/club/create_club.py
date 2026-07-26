# app/services/club/create_club.py

"""
app/services/club/create_club.py

Creates a club in the given branch. Authorization (branch membership or
club.create permission) is enforced by the router before this is called —
this function assumes the caller is already permitted to act in this
branch/org and focuses purely on the write.

The creator automatically becomes the club leader and is added as the
first member.
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.club import Club, ClubMember
from app.services.errors import AppError
from app.services.payment import enforce_plan_limit


async def create_club(
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    name: str,
    description: str | None,
    privacy: str,
    created_by: str,
) -> Club:
    # Validate branch belongs to org
    branch_result = await session.execute(
        select(Branch).where(
            Branch.id == branch_id,
            Branch.organization_id == organization_id,
        )
    )
    if not branch_result.scalar_one_or_none():
        raise AppError(
            "Branch not found in this organization.",
            status.HTTP_404_NOT_FOUND,
        )

    existing_count = (await session.execute(
        select(func.count()).select_from(Club).where(Club.organization_id == organization_id)
    )).scalar_one()

    await enforce_plan_limit(
        session=session, organization_id=organization_id, limit_key="max_clubs", current_count=existing_count,
    )

    # Check name uniqueness within org
    existing = await session.execute(
        select(Club).where(
            Club.organization_id == organization_id,
            Club.name == name.strip(),
        )
    )
    if existing.scalar_one_or_none():
        raise AppError(
            f"A club named '{name}' already exists in this organization.",
            status.HTTP_409_CONFLICT,
        )

    club = Club(
        name=name.strip(),
        description=description,
        privacy=privacy,
        organization_id=organization_id,
        branch_id=branch_id,
        created_by=created_by,
        leader_id=created_by,  # creator is the leader
        member_count=0,
    )
    session.add(club)
    await session.flush()

    # Auto-add creator as first member
    session.add(ClubMember(club_id=club.id, user_id=created_by))
    club.member_count = 1
    await session.flush()

    return club