"""
app/services/post/delete_post.py
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.hashtag import remove_all_post_hashtags
from app.services.post._helpers import get_post_or_404
from app.services.storage import release_media_asset
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def delete_post(*, session: AsyncSession, organization_id: str, post_id: str) -> None:
    """Hard delete — cascades to likes/comments/shares/saves/hashtags via FK ondelete."""
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)

    media_url = post.media_url  # capture before delete

    await remove_all_post_hashtags(session=session, post_id=post_id)
    await session.delete(post)
    await session.flush()

    if media_url:
        try:
            await release_media_asset(session=session, object_key=extract_object_key(media_url))
        except Exception:
            logger.error(
                "Failed to release post media asset", exc_info=True,
                extra={"post_id": post_id},
            )

    logger.info("Post deleted", extra={"post_id": post_id, "org_id": organization_id})