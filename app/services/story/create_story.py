"""
app/services/story/create_story.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MEDIA_TYPES
from app.models import Story
from app.services.story._helpers import validate_org_and_branch
from app.services.post.media import validate_post_media
from app.services.storage import attach_media_asset, confirm_upload
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)

STORY_LIFETIME = timedelta(hours=24)


async def create_story(
    *, session: AsyncSession, organization_id: str, user_id: str, branch_id: str | None,
    media_url: str, media_type: str,
) -> Story:
    if media_type not in MEDIA_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"media_type must be one of {MEDIA_TYPES}.")

    validate_post_media([media_url], media_type)
    await validate_org_and_branch(session=session, organization_id=organization_id, branch_id=branch_id)

    object_key = extract_object_key(media_url)
    if not confirm_upload(object_key=object_key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Media upload not found — upload may have failed or not completed yet.")

    now = datetime.now(timezone.utc)
    story = Story(
        user_id=user_id, organization_id=organization_id, branch_id=branch_id,
        media_url=media_url, media_type=media_type, expires_at=now + STORY_LIFETIME,
    )
    session.add(story)
    await session.flush()
    await session.refresh(story)

    # Mark the uploaded media as attached so the pending-asset sweep
    # leaves it alone. Stories are the one domain where media_url is
    # required (not optional like posts/challenges/events), so this
    # is always called, never conditionally skipped.
    await attach_media_asset(
        session=session, object_key=object_key,
        attached_to_type="story", attached_to_id=story.id,
    )

    logger.info("Story created", extra={"story_id": story.id, "org_id": organization_id, "branch_id": branch_id})
    return story