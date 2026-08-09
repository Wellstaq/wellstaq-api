"""
app/services/post/edit_post.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.datetime import ensure_utc
from app.models import Post
from app.services.hashtag import sync_post_hashtags
from app.services.post._helpers import get_post_or_404
from app.services.storage import attach_media_asset, confirm_upload, release_media_asset
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)

EDIT_WINDOW = timedelta(hours=1)


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

    if datetime.now(timezone.utc) - ensure_utc(post.created_at) > EDIT_WINDOW:
        logger.info("Post edit rejected: window expired", extra={"post_id": post.id})
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This post can no longer be edited (1 hour edit window has expired)."
        )

    if content is not None:
        post.content = content.strip()
        await sync_post_hashtags(session=session, organization_id=organization_id, post_id=post.id, content=post.content)

    if media_url is not None:
        object_key = extract_object_key(media_url)
        if not confirm_upload(object_key=object_key):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Media upload not found — upload may have failed or not completed yet.")

        old_media_url = post.media_url
        post.media_url = media_url

        # Mark the new upload as attached so the sweep job won't reap it.
        await attach_media_asset(
            session=session, object_key=object_key,
            attached_to_type="post", attached_to_id=post.id,
        )

        # Release the old media now that it's no longer referenced —
        # previously this was silently overwritten with no cleanup at all.
        if old_media_url and old_media_url != media_url:
            try:
                await release_media_asset(session=session, object_key=extract_object_key(old_media_url))
            except Exception:
                logger.error(
                    "Failed to release old post media asset", exc_info=True,
                    extra={"post_id": post.id},
                )

    if media_type is not None:
        post.media_type = media_type

    await session.flush()
    await session.refresh(post)

    logger.info("Post edited", extra={"post_id": post.id})
    return post