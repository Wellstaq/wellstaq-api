"""
app/services/club/create_club.py

Creates a club in the given branch. Authorization (branch membership or
club.create permission) is enforced by the router before this is called —
this function assumes the caller is already permitted to act in this
branch/org and focuses purely on the write.

category is a fixed enum validated at the schema layer (see
app/schemas/club.py VALID_CATEGORIES) — no lookup or FK check needed
here, unlike the earlier categories-table approach.

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
from app.services.storage import attach_media_asset, confirm_upload
from app.services.storage._helpers import extract_object_key


async def create_club(
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    name: str,
    description: str | None,
    image_url: str | None,
    privacy: str,
    category: str,
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

    object_key = None
    if image_url is not None:
        object_key = extract_object_key(image_url)
        if not confirm_upload(object_key=object_key):
            raise AppError(
                "Image upload not found — upload may have failed or not completed yet.",
                status.HTTP_400_BAD_REQUEST,
            )

    club = Club(
        name=name.strip(),
        description=description,
        image_url=image_url,
        privacy=privacy,
        category=category,
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
    await session.refresh(club)

    # Mark the uploaded image (if any) as attached so the pending-asset
    # sweep job leaves it alone. No-ops cleanly if object_key is None.
    await attach_media_asset(
        session=session, object_key=object_key,
        attached_to_type="club", attached_to_id=club.id,
    )

    return club