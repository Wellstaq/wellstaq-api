"""
app/services/post/save_service.py
"""

from __future__ import annotations

from app.auth.dependencies import CurrentUser
from app.models import Post, PostSave
from app.services.post._helpers import check_post_branch_access, get_post_or_404
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def save_post(
    *, session: AsyncSession, organization_id: str, post_id: str, current_user: CurrentUser
) -> bool:
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)
    await check_post_branch_access(db=session, current_user=current_user, post=post)

    existing = await session.execute(
        select(PostSave).where(PostSave.post_id == post_id, PostSave.user_id == current_user.user_id)
    )
    if existing.scalar_one_or_none() is not None:
        return True

    session.add(PostSave(organization_id=organization_id, post_id=post_id, user_id=current_user.user_id))
    await session.flush()
    return True


async def unsave_post(
    *, session: AsyncSession, organization_id: str, post_id: str, current_user: CurrentUser
) -> bool:
    post = await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)
    await check_post_branch_access(db=session, current_user=current_user, post=post)

    result = await session.execute(
        select(PostSave).where(PostSave.post_id == post_id, PostSave.user_id == current_user.user_id)
    )
    save = result.scalar_one_or_none()
    if save is None:
        return False

    await session.delete(save)
    await session.flush()
    return False


async def get_saved_posts(
    *, session: AsyncSession, organization_id: str, user_id: str, offset: int = 0, limit: int = 50
) -> tuple[list[Post], int]:
    query = (
        select(Post)
        .join(PostSave, PostSave.post_id == Post.id)
        .where(PostSave.user_id == user_id, Post.organization_id == organization_id)
        .order_by(PostSave.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    count_query = (
        select(func.count())
        .select_from(PostSave)
        .join(Post, Post.id == PostSave.post_id)
        .where(PostSave.user_id == user_id, Post.organization_id == organization_id)
    )

    total = (await session.execute(count_query)).scalar_one()
    posts = (await session.execute(query)).scalars().all()
    return list(posts), total