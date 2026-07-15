"""
app/services/post/delete_post.py
"""

from __future__ import annotations

import logging

from app.services.post._helpers import get_post_or_404
from app.services.hashtag import remove_all_post_hashtags
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def delete_post(*, session: AsyncSession, organization_id: str, post_id: str) -> None:
    """Hard delete — cascades to likes/comments/shares/saves/hashtags via FK ondelete."""
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)

    # TODO(s3): if media_url points at an S3 object owned by this post
    # (not a shared/reused asset), enqueue deletion of the object here
    # rather than deleting inline — don't want a slow S3 call blocking
    # the request/transaction.
    
    await remove_all_post_hashtags(session=session, post_id=post_id)
    await session.delete(post)
    await session.flush()

    logger.info("Post deleted", extra={"post_id": post_id, "org_id": organization_id})