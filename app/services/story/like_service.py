"""Idempotent story reactions, scoped to live and visible stories."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.models import StoryLike
from app.services.story.get_story import get_story
from app.services.story._helpers import check_story_branch_access


async def set_story_like(*, session: AsyncSession, organization_id: str, story_id: str,
                         current_user: CurrentUser, liked: bool) -> int:
    story = await get_story(session=session, organization_id=organization_id, story_id=story_id)
    await check_story_branch_access(db=session, current_user=current_user, story=story)
    existing = (await session.execute(select(StoryLike).where(
        StoryLike.story_id == story_id, StoryLike.user_id == current_user.user_id
    ))).scalar_one_or_none()
    if liked and existing is None:
        session.add(StoryLike(organization_id=organization_id, story_id=story_id, user_id=current_user.user_id))
        story.like_count += 1
        await session.flush()
    elif not liked and existing is not None:
        await session.delete(existing)
        story.like_count = max(0, story.like_count - 1)
        await session.flush()
    return story.like_count
