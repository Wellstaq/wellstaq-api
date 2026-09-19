"""
app/services/post/create_post.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Post
from app.services.activity import log_activity
from app.services.hashtag import sync_post_hashtags
from app.services.post._helpers import validate_org_and_branch
from app.services.post.media import validate_post_media
from app.services.storage import attach_media_asset, confirm_upload
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def create_post(
    *,
    session: AsyncSession,
    organization_id: str,
    user_id: str,
    branch_id: str | None,
    content: str | None,
    media_url: str | None,
    media_type: str | None,
    media_urls: list[str] | None = None,
) -> Post:
    """
    branch_id=None => org-level feed post. club_id=None => general feed post.
    Both are optional and independent of each other.
    """
    await validate_org_and_branch(session=session, organization_id=organization_id, branch_id=branch_id)

    urls = media_urls if media_urls is not None else ([media_url] if media_url else [])
    validate_post_media(urls, media_type)
    object_keys = [extract_object_key(url) for url in urls]
    for object_key in object_keys:
        if not object_key or not confirm_upload(object_key=object_key):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Media upload not found — upload may have failed or not completed yet.")

    post = Post(
        user_id=user_id,
        organization_id=organization_id,
        branch_id=branch_id,
        content=content.strip() if content else None,
        media_url=urls[0] if urls else None,
        media_type=media_type,
        media_urls=urls,
        like_count=0,
        comment_count=0,
    )
    session.add(post)
    await session.flush()
    await session.refresh(post)
    await sync_post_hashtags(session=session, organization_id=organization_id, post_id=post.id, content=post.content)
    await session.refresh(post)

    # Mark the uploaded media (if any) as attached so the pending-asset
    # sweep job leaves it alone. No-ops cleanly if object_key is None.
    for object_key in object_keys:
        await attach_media_asset(
            session=session, object_key=object_key,
            attached_to_type="post", attached_to_id=post.id,
        )

    # TODO(redis): bump a trending/recent-activity counter here if you add one.
    # TODO(background-job): enqueue feed fan-out job if you move to a
    # push/fan-out-on-write feed model instead of fan-out-on-read.

    await log_activity(
        session=session, user_id=user_id, organization_id=organization_id,
        activity_type="created_post", reference_id=post.id, reference_type="post",
    )
    logger.info(
        "Post created",
        extra={"post_id": post.id, "org_id": organization_id, "branch_id": branch_id, "user_id": user_id},
    )
    return post