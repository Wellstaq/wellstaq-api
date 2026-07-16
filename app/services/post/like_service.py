"""
app/services/post/like_service.py
"""

from __future__ import annotations

import logging

from app.auth.dependencies import CurrentUser
from app.models import PostLike, User
from app.services.post._helpers import check_post_branch_access, get_post_or_404
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def like_post(
    *, session: AsyncSession, organization_id: str, post_id: str, current_user: CurrentUser
) -> tuple[bool, int]:
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)
    await check_post_branch_access(db=session, current_user=current_user, post=post)

    existing = await session.execute(
        select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == current_user.user_id)
    )
    if existing.scalar_one_or_none() is not None:
        return True, post.like_count

    like = PostLike(organization_id=organization_id, post_id=post_id, user_id=current_user.user_id)
    session.add(like)
    post.like_count += 1
    await session.flush()

    return True, post.like_count


async def unlike_post(
    *, session: AsyncSession, organization_id: str, post_id: str, current_user: CurrentUser
) -> tuple[bool, int]:
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)
    await check_post_branch_access(db=session, current_user=current_user, post=post)

    result = await session.execute(
        select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == current_user.user_id)
    )
    like = result.scalar_one_or_none()
    if like is None:
        return False, post.like_count

    await session.delete(like)
    post.like_count = max(0, post.like_count - 1)
    await session.flush()

    return False, post.like_count


async def get_post_likers(
    *, session: AsyncSession, organization_id: str, post_id: str, offset: int = 0, limit: int = 50
) -> tuple[list[User], int]:
    await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)

    query = (
        select(User)
        .join(PostLike, PostLike.user_id == User.id)
        .where(PostLike.post_id == post_id)
        .order_by(PostLike.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    count_query = select(func.count()).select_from(PostLike).where(PostLike.post_id == post_id)

    total = (await session.execute(count_query)).scalar_one()
    users = (await session.execute(query)).scalars().all()
    return list(users), total