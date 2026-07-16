"""
app/services/post/get_post.py
"""

from __future__ import annotations

from app.models import Post
from app.services.post._helpers import get_post_or_404
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_post(*, session: AsyncSession, organization_id: str, post_id: str) -> Post:
    return await get_post_or_404(session=session, organization_id=organization_id, post_id=post_id)


async def get_posts(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str | None = None,
    viewer_branch_id: str | None = None,
    viewable_branch_ids: list[str] | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Post], int]:
    """
    branch_id: explicit filter from the caller (already access-checked).

    When branch_id is NOT set, results are restricted based on
    viewable_branch_ids:
      - viewable_branch_ids is None  -> caller sees everything (super_admin
        or an org-wide post.view_all grant) — no branch filter.
      - viewable_branch_ids is a list -> caller sees org-level posts +
        their own branch + every branch in this list (dedup'd).
    """
    query = select(Post).where(Post.organization_id == organization_id)
    count_query = select(func.count()).select_from(Post).where(Post.organization_id == organization_id)

    if branch_id is not None:
        query = query.where(Post.branch_id == branch_id)
        count_query = count_query.where(Post.branch_id == branch_id)
    elif viewable_branch_ids is not None:
        allowed = set(viewable_branch_ids)
        if viewer_branch_id is not None:
            allowed.add(viewer_branch_id)
        visibility = or_(Post.branch_id.is_(None), Post.branch_id.in_(allowed))
        query = query.where(visibility)
        count_query = count_query.where(visibility)
    # else: viewable_branch_ids is None -> no branch filter, sees everything

    query = query.order_by(Post.created_at.desc()).offset(offset).limit(limit)

    total = (await session.execute(count_query)).scalar_one()
    posts = (await session.execute(query)).scalars().all()
    return list(posts), total


async def get_posts_by_user(
    *,
    session: AsyncSession,
    organization_id: str,
    user_id: str,
    viewer_branch_id: str | None = None,
    viewable_branch_ids: list[str] | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Post], int]:
    """Same visibility rule as get_posts, applied to one author's posts."""
    conditions = [Post.organization_id == organization_id, Post.user_id == user_id]

    if viewable_branch_ids is not None:
        allowed = set(viewable_branch_ids)
        if viewer_branch_id is not None:
            allowed.add(viewer_branch_id)
        conditions.append(or_(Post.branch_id.is_(None), Post.branch_id.in_(allowed)))

    query = select(Post).where(*conditions).order_by(Post.created_at.desc()).offset(offset).limit(limit)
    count_query = select(func.count()).select_from(Post).where(*conditions)

    total = (await session.execute(count_query)).scalar_one()
    posts = (await session.execute(query)).scalars().all()
    return list(posts), total