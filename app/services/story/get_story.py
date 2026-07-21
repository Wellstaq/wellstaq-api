"""
app/services/story/get_story.py

All reads filter expires_at > now — expired stories are purged by a
background job (see app/jobs/media/story_purge.py), but between
creation and purge, queries also exclude them defensively so a story
never appears "active" past its expiry even if the purge job is
temporarily behind.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Story
from app.services.story._helpers import get_story_or_404


async def get_story(*, session: AsyncSession, organization_id: str, story_id: str) -> Story:
    story = await get_story_or_404(session=session, organization_id=organization_id, story_id=story_id)
    if story.expires_at <= datetime.now(timezone.utc):
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Story has expired.")
    return story


async def get_stories(
    *, session: AsyncSession, organization_id: str, branch_id: str | None = None,
    viewer_branch_id: str | None = None, is_super_admin: bool = False,
    offset: int = 0, limit: int = 50,
) -> tuple[list[Story], int]:
    now = datetime.now(timezone.utc)
    query = select(Story).where(Story.organization_id == organization_id, Story.expires_at > now)
    count_query = select(func.count()).select_from(Story).where(
        Story.organization_id == organization_id, Story.expires_at > now
    )

    if branch_id is not None:
        query = query.where(Story.branch_id == branch_id)
        count_query = count_query.where(Story.branch_id == branch_id)
    elif not is_super_admin:
        visibility = or_(Story.branch_id.is_(None), Story.branch_id == viewer_branch_id)
        query = query.where(visibility)
        count_query = count_query.where(visibility)

    query = query.order_by(Story.created_at.desc()).offset(offset).limit(limit)

    total = (await session.execute(count_query)).scalar_one()
    stories = (await session.execute(query)).scalars().all()
    return list(stories), total


async def get_my_stories(*, session: AsyncSession, user_id: str, offset: int = 0, limit: int = 50) -> tuple[list[Story], int]:
    """A user's own stories — including ones they'd otherwise not see
    on the feed due to branch scope, since it's their own content."""
    now = datetime.now(timezone.utc)
    query = (
        select(Story).where(Story.user_id == user_id, Story.expires_at > now)
        .order_by(Story.created_at.desc()).offset(offset).limit(limit)
    )
    count_query = select(func.count()).select_from(Story).where(Story.user_id == user_id, Story.expires_at > now)

    total = (await session.execute(count_query)).scalar_one()
    stories = (await session.execute(query)).scalars().all()
    return list(stories), total