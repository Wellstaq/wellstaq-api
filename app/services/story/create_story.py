"""
app/services/story/create_story.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from app.models import Story
from app.services.story._helpers import  validate_org_and_branch
from app.core.constants import MEDIA_TYPES
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

STORY_LIFETIME = timedelta(hours=24)


async def create_story(
    *, session: AsyncSession, organization_id: str, user_id: str, branch_id: str | None,
    media_url: str, media_type: str,
) -> Story:
    if media_type not in MEDIA_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"media_type must be one of {MEDIA_TYPES}.")

    await validate_org_and_branch(session=session, organization_id=organization_id, branch_id=branch_id)

    now = datetime.now(timezone.utc)
    story = Story(
        user_id=user_id, organization_id=organization_id, branch_id=branch_id,
        media_url=media_url, media_type=media_type, expires_at=now + STORY_LIFETIME,
    )
    session.add(story)
    await session.flush()
    await session.refresh(story)

    logger.info("Story created", extra={"story_id": story.id, "org_id": organization_id, "branch_id": branch_id})
    return story