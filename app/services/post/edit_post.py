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
from app.services.post.media import validate_post_media
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
    media_urls: list[str] | None = None,
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

    if media_url is not None or media_urls is not None:
        urls = media_urls if media_urls is not None else [media_url]
        effective_type = media_type or ("image" if media_urls is not None else post.media_type)
        validate_post_media(urls, effective_type)
        old_urls = post.media_urls or ([post.media_url] if post.media_url else [])
        new_keys = [extract_object_key(url) for url in urls]
        for key in new_keys:
            if not key or not confirm_upload(object_key=key):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Media upload not found — upload may have failed or not completed yet.")
        post.media_url = urls[0]
        post.media_urls = urls
        post.media_type = effective_type
        for key in new_keys:
            await attach_media_asset(session=session, object_key=key, attached_to_type="post", attached_to_id=post.id)
        for url in set(old_urls) - set(urls):
            try:
                await release_media_asset(session=session, object_key=extract_object_key(url))
            except Exception:
                logger.error("Failed to release old post media asset", exc_info=True, extra={"post_id": post.id})
    elif media_type is not None and media_type != post.media_type:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Changing media_type requires a new media upload.")

    await session.flush()
    await session.refresh(post)

    logger.info("Post edited", extra={"post_id": post.id})
    return post