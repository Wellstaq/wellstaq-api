"""
app/services/club/create_club.py

Anyone in the org can create a club, but they must belong to the branch
they want to create it under. The creator automatically becomes the
club leader and is added as the first member.
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.club import Club, ClubMember
from app.models import User
from app.services.errors import AppError


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

    # Confirm creator belongs to this branch
    user_result = await session.execute(
        select(User).where(
            User.id == created_by,
            User.organization_id == organization_id,
            User.branch_id == branch_id,
        )
    )
    if not user_result.scalar_one_or_none():
        raise AppError(
            "You must belong to this branch to create a club here.",
            status.HTTP_403_FORBIDDEN,
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