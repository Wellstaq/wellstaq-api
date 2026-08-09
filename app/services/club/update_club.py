"""
app/services/club/update_club.py

Partial update (PATCH) for a club.

branch_id is intentionally not updatable — a club cannot be moved
between branches after creation. category is optional on PATCH and,
when supplied, is already validated against the fixed enum at the
schema layer — no lookup needed here. This function only flushes —
the router/caller commits.
"""

from __future__ import annotations

import logging

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club
from app.services.errors import AppError
from app.services.storage import attach_media_asset, confirm_upload, release_media_asset
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def edit_club(
    session: AsyncSession,
    organization_id: str,
    club_id: str,
    name: str | None,
    description: str | None,
    image_url: str | None,
    privacy: str | None,
    category: str | None,
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

    if category is not None:
        club.category = category

    if image_url is not None and image_url != club.image_url:
        object_key = extract_object_key(image_url)
        if not confirm_upload(object_key=object_key):
            raise AppError(
                "Image upload not found — upload may have failed or not completed yet.",
                status.HTTP_400_BAD_REQUEST,
            )

        old_image_url = club.image_url
        club.image_url = image_url

        # Mark the new upload as attached so the pending-sweep won't reap it.
        await attach_media_asset(
            session=session, object_key=object_key,
            attached_to_type="club", attached_to_id=club.id,
        )

        # Release the old image now that it's no longer referenced.
        if old_image_url:
            try:
                await release_media_asset(session=session, object_key=extract_object_key(old_image_url))
            except Exception:
                logger.error(
                    "Failed to release old club image media asset", exc_info=True,
                    extra={"club_id": club_id},
                )

    if privacy is not None:
        club.privacy = privacy

    await session.flush()
    return club