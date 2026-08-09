"""
app/services/club/delete_club.py

Delete a club.

All ClubMember rows are cascade-deleted by the DB (ondelete=CASCADE
on ClubMember.club_id). Member users are not deleted — they just lose
their club membership and are left without a club, which is acceptable
(clubs are optional affinity groups, not required like departments).

This function only flushes — the router/caller commits. The S3 release
therefore happens BEFORE the commit, in the same transaction as the
delete, unlike the earlier commit-per-step version.
"""

from __future__ import annotations

import logging

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.club import Club
from app.services.errors import AppError
from app.services.storage import release_media_asset
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


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

    image_url = club.image_url  # capture before delete

    await session.delete(club)
    await session.flush()

    if image_url:
        try:
            await release_media_asset(session=session, object_key=extract_object_key(image_url))
        except Exception:
            logger.error(
                "Failed to release club image media asset", exc_info=True,
                extra={"club_id": club_id},
            )

    return {
        "club_id": club_id,
        "organization_id": organization_id,
    }