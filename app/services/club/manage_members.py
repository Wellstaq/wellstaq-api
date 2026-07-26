"""
app/services/club/manage_members.py

Club membership management.

join_club:
  - User must belong to the branch that owns the club
  - Rejects private clubs (invite-only)
  - Prevents joining the same club twice
  - A user can belong to multiple clubs simultaneously

leave_club:
  - Confirms the user is actually a member before removing
  - Club leader cannot leave — they must transfer or delete the club

remove_club_member:
  - Called by the club leader only
  - Leader can remove any member without permission checks
  - Cannot remove themselves (use leave or delete instead)

get_club_members:
  - Paginated User rows for a club
"""

from __future__ import annotations

from fastapi import status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club, ClubMember
from app.models import User
from app.services.errors import AppError
from app.services.activity import log_activity


async def join_club(
    session: AsyncSession,
    organization_id: str,
    club_id: str,
    user_id: str,
) -> None:
    # Load target club
    club_result = await session.execute(
        select(Club).where(
            Club.id == club_id,
            Club.organization_id == organization_id,
        )
    )
    club = club_result.scalar_one_or_none()
    if not club:
        raise AppError("Club not found.", status.HTTP_404_NOT_FOUND)

    # Reject private clubs
    if club.privacy == "private":
        raise AppError(
            "This club is private. You need an invitation to join.",
            status.HTTP_403_FORBIDDEN,
        )

    # Confirm user belongs to the branch that owns this club
    user_result = await session.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == organization_id,
            User.branch_id == club.branch_id,
        )
    )
    if not user_result.scalar_one_or_none():
        raise AppError(
            "You must belong to this club's branch to join it.",
            status.HTTP_403_FORBIDDEN,
        )

    # Prevent joining the same club twice
    existing_result = await session.execute(
        select(ClubMember).where(
            ClubMember.club_id == club_id,
            ClubMember.user_id == user_id,
        )
    )
    if existing_result.scalar_one_or_none():
        raise AppError(
            "User is already a member of this club.",
            status.HTTP_409_CONFLICT,
        )

    session.add(ClubMember(club_id=club_id, user_id=user_id))
    await session.flush()
    #add to activity feed
    club.member_count += 1
    await log_activity(
        session=session, user_id=user_id, organization_id=organization_id,
        activity_type="joined_club", reference_id=club_id, reference_type="club",
    )


async def leave_club(
    session: AsyncSession,
    organization_id: str,
    club_id: str,
    user_id: str,
) -> None:
    # Validate club belongs to org
    club_result = await session.execute(
        select(Club).where(
            Club.id == club_id,
            Club.organization_id == organization_id,
        )
    )
    club = club_result.scalar_one_or_none()
    if not club:
        raise AppError("Club not found.", status.HTTP_404_NOT_FOUND)

    # Club leader cannot leave — they must delete the club or transfer leadership
    if club.leader_id == user_id:
        raise AppError(
            "You are the club leader and cannot leave. "
            "Delete the club or transfer leadership first.",
            status.HTTP_409_CONFLICT,
        )

    # Confirm the user is actually a member
    member_result = await session.execute(
        select(ClubMember).where(
            ClubMember.club_id == club_id,
            ClubMember.user_id == user_id,
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise AppError(
            "User is not a member of this club.",
            status.HTTP_404_NOT_FOUND,
        )

    await session.delete(member)
    club.member_count = max(0, club.member_count - 1)
    await session.flush()


async def remove_club_member(
    session: AsyncSession,
    organization_id: str,
    club_id: str,
    target_user_id: str,
    requesting_user_id: str,
) -> None:
    """
    Removes a member from a club. Authorization (leader, or club.update
    permission) is enforced by the router before this is called.
    """
    club_result = await session.execute(
        select(Club).where(
            Club.id == club_id,
            Club.organization_id == organization_id,
        )
    )
    club = club_result.scalar_one_or_none()
    if not club:
        raise AppError("Club not found.", status.HTTP_404_NOT_FOUND)

    # A permission-holder removing the leader isn't a supported flow —
    # leadership changes go through transfer/delete, not member removal.
    if target_user_id == club.leader_id:
        raise AppError(
            "The club leader cannot be removed this way. "
            "Transfer leadership or delete the club instead.",
            status.HTTP_400_BAD_REQUEST,
        )

    member_result = await session.execute(
        select(ClubMember).where(
            ClubMember.club_id == club_id,
            ClubMember.user_id == target_user_id,
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise AppError(
            "User is not a member of this club.",
            status.HTTP_404_NOT_FOUND,
        )

    await session.delete(member)
    club.member_count = max(0, club.member_count - 1)
    await session.flush()
async def get_club_members(
    session: AsyncSession,
    organization_id: str,
    club_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[User], int]:
    club_result = await session.execute(
        select(Club).where(
            Club.id == club_id,
            Club.organization_id == organization_id,
        )
    )
    if not club_result.scalar_one_or_none():
        raise AppError("Club not found.", status.HTTP_404_NOT_FOUND)

    count_result = await session.execute(
        select(func.count()).select_from(ClubMember).where(
            ClubMember.club_id == club_id
        )
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(User)
        .join(ClubMember, ClubMember.user_id == User.id)
        .where(ClubMember.club_id == club_id)
        .order_by(User.first_name)
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all()), total


async def is_club_member(
    session: AsyncSession,
    club_id: str,
    user_id: str,
) -> bool:
    result = await session.execute(
        select(ClubMember).where(
            ClubMember.club_id == club_id,
            ClubMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None