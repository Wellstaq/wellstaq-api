"""
app/services/post/create_post.py
"""

from __future__ import annotations

import logging

from app.models import Post
from app.services.post._helpers import validate_org_and_branch
from app.services.hashtag import sync_post_hashtags
from sqlalchemy.ext.asyncio import AsyncSession

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
) -> Post:
    """
    branch_id=None => org-level feed post. club_id=None => general feed post.
    Both are optional and independent of each other.
    """
    await validate_org_and_branch(session=session, organization_id=organization_id, branch_id=branch_id)

   
    post = Post(
        user_id=user_id,
        organization_id=organization_id,
        branch_id=branch_id,
        content=content.strip() if content else None,
        media_url=media_url,
        media_type=media_type,
        like_count=0,
        comment_count=0,
    )
    session.add(post)
    await session.flush()
    await session.refresh(post)
    await sync_post_hashtags(session=session, organization_id=organization_id, post_id=post.id, content=post.content)
    await session.refresh(post)

    # TODO(redis): bump a trending/recent-activity counter here if you add one.
    # TODO(background-job): enqueue feed fan-out job if you move to a
    # push/fan-out-on-write feed model instead of fan-out-on-read.

    logger.info(
        "Post created",
        extra={"post_id": post.id, "org_id": organization_id, "branch_id": branch_id, "user_id": user_id},
    )
    return post