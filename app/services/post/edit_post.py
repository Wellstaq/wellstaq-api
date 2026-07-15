"""
app/services/post/edit_post.py
"""

from __future__ import annotations

import logging

from app.models import Post
from app.services.post._helpers import get_post_or_404
from app.services.hashtag import sync_post_hashtags
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def edit_post(
    *,
    session: AsyncSession,
    organization_id: str,
    post_id: str,
    content: str | None,
    media_url: str | None,
    media_type: str | None,
) -> Post:
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)

    if content is not None:
        post.content = content.strip()
        await sync_post_hashtags(session=session, organization_id=organization_id, post_id=post.id, content=post.content)
    if media_url is not None:
        post.media_url = media_url
    if media_type is not None:
        post.media_type = media_type

    await session.flush()
    await session.refresh(post)

    logger.info("Post edited", extra={"post_id": post.id})
    return post