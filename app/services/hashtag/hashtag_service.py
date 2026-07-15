"""
app/services/hashtag/hashtag_service.py

Owns Hashtag row lifecycle and the Post<->Hashtag link (PostHashtag),
including keeping Hashtag.total_post_count in sync.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hashtag, PostHashtag
from app.services.hashtag._helpers import extract_hashtags

logger = logging.getLogger(__name__)


async def get_or_create_hashtag(*, session: AsyncSession, organization_id: str, tag_name: str) -> Hashtag:
    """tag_name must already be normalized (see _helpers.normalize_tag) —
    this function does not re-normalize, to keep it a pure get-or-create."""
    result = await session.execute(
        select(Hashtag).where(Hashtag.organization_id == organization_id, Hashtag.tag_name == tag_name)
    )
    hashtag = result.scalar_one_or_none()
    if hashtag is not None:
        return hashtag

    hashtag = Hashtag(organization_id=organization_id, tag_name=tag_name, total_post_count=0)
    session.add(hashtag)
    await session.flush()
    return hashtag


async def sync_post_hashtags(*, session: AsyncSession, organization_id: str, post_id: str, content: str | None) -> None:
    """
    Reconciles PostHashtag rows for a post against the #tags currently
    present in its content. Call this on both create (existing=empty set)
    and edit (diffs against whatever was there before).

    - Tags newly present: get-or-create the Hashtag, link it, +1 count.
    - Tags no longer present: unlink, -1 count.
    - Tags unchanged: left alone entirely (no redundant delete+reinsert).
    """
    new_tags = extract_hashtags(content)

    existing_result = await session.execute(
        select(PostHashtag, Hashtag)
        .join(Hashtag, Hashtag.id == PostHashtag.hashtag_id)
        .where(PostHashtag.post_id == post_id)
    )
    existing_rows = existing_result.all()
    existing_by_name: dict[str, tuple[PostHashtag, Hashtag]] = {
        hashtag.tag_name: (post_hashtag, hashtag) for post_hashtag, hashtag in existing_rows
    }
    existing_names = set(existing_by_name.keys())

    to_add = new_tags - existing_names
    to_remove = existing_names - new_tags

    for tag_name in to_add:
        hashtag = await get_or_create_hashtag(session=session, organization_id=organization_id, tag_name=tag_name)
        session.add(PostHashtag(organization_id=organization_id, post_id=post_id, hashtag_id=hashtag.id))
        hashtag.total_post_count += 1

    for tag_name in to_remove:
        post_hashtag, hashtag = existing_by_name[tag_name]
        await session.delete(post_hashtag)
        hashtag.total_post_count = max(0, hashtag.total_post_count - 1)

    if to_add or to_remove:
        await session.flush()
        logger.info(
            "Post hashtags synced",
            extra={"post_id": post_id, "added": list(to_add), "removed": list(to_remove)},
        )


async def remove_all_post_hashtags(*, session: AsyncSession, post_id: str) -> None:
    """
    Call this BEFORE deleting a post. PostHashtag rows themselves cascade-
    delete automatically via FK ondelete='CASCADE' when the post is
    deleted — but that cascade does NOT decrement Hashtag.total_post_count,
    since a DB-level cascade can't run application logic. This function
    does the count decrement; the row deletion still happens via cascade
    when the caller deletes the Post afterward (deleting here too is
    harmless — cascade just finds nothing left to delete).
    """
    result = await session.execute(
        select(PostHashtag, Hashtag)
        .join(Hashtag, Hashtag.id == PostHashtag.hashtag_id)
        .where(PostHashtag.post_id == post_id)
    )
    rows = result.all()
    for _post_hashtag, hashtag in rows:
        hashtag.total_post_count = max(0, hashtag.total_post_count - 1)

    if rows:
        await session.flush()