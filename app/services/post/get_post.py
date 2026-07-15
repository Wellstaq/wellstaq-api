"""
app/services/post/get_post.py
"""

from __future__ import annotations

from app.models import Post
from app.services.post._helpers import get_post_or_404
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_post(*, session: AsyncSession, organization_id: str, post_id: str) -> Post:
    return await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)


async def get_posts(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Post], int]:
    """
    branch_id is optional filters:
      - branch_id omitted -> all posts in org (org-level + every branch)
      - branch_id="" or a sentinel isn't supported; pass the actual branch id
        to filter to just that branch's posts, or None to not filter on it.
    """
    query = select(Post).where(Post.organization_id == organization_id)
    count_query = select(func.count()).select_from(Post).where(Post.organization_id == organization_id)

    if branch_id is not None:
        query = query.where(Post.branch_id == branch_id)
        count_query = count_query.where(Post.branch_id == branch_id)

   
    query = query.order_by(Post.created_at.desc()).offset(offset).limit(limit)

    total = (await session.execute(count_query)).scalar_one()
    posts = (await session.execute(query)).scalars().all()
    return list(posts), total


async def get_posts_by_user(
    *,
    session: AsyncSession,
    organization_id: str,
    user_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Post], int]:
    """All posts authored by a specific user, across branches"""
    query = (
        select(Post)
        .where(Post.organization_id == organization_id, Post.user_id == user_id)
        .order_by(Post.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    count_query = (
        select(func.count())
        .select_from(Post)
        .where(Post.organization_id == organization_id, Post.user_id == user_id)
    )

    total = (await session.execute(count_query)).scalar_one()
    posts = (await session.execute(query)).scalars().all()
    return list(posts), total