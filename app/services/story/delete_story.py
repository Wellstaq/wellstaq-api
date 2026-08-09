"""
app/services/story/delete_story.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.story._helpers import get_story_or_404
from app.services.storage import release_media_asset
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def delete_story(*, session: AsyncSession, organization_id: str, story_id: str, requesting_user_id: str, is_moderator: bool) -> None:
    story = await get_story_or_404(session=session, organization_id=organization_id, story_id=story_id)

    if story.user_id != requesting_user_id and not is_moderator:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete your own stories.")

    media_url = story.media_url  # capture before delete

    await session.delete(story)
    await session.flush()

    if media_url:
        try:
            await release_media_asset(session=session, object_key=extract_object_key(media_url))
        except Exception:
            logger.error(
                "Failed to release story media asset", exc_info=True,
                extra={"story_id": story_id},
            )

    logger.info("Story deleted", extra={"story_id": story_id})