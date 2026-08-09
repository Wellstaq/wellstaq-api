"""
app/services/post/get_post.py
"""

from __future__ import annotations

from datetime import date, datetime,  time as dt_time

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
    org_only: bool = False,
    viewer_branch_id: str | None = None,
    viewable_branch_ids: list[str] | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Post], int]:
    """
    org_only=True  -> Overview scope: ONLY org-wide posts (branch_id IS NULL).
    branch_id set  -> Branch scope: that branch's posts + org-wide posts.
    neither set, viewable_branch_ids given -> admin "view_all" combining
      org-wide + every branch they're granted, same as before.
    neither set, viewable_branch_ids is None -> super_admin/org-wide
      view_all grant, no filter at all.
    """
    query = select(Post).where(Post.organization_id == organization_id)
    count_query = select(func.count()).select_from(Post).where(Post.organization_id == organization_id)

    if org_only:
        query = query.where(Post.branch_id.is_(None))
        count_query = count_query.where(Post.branch_id.is_(None))
    elif branch_id is not None:
        visibility = or_(Post.branch_id == branch_id, Post.branch_id.is_(None))
        query = query.where(visibility)
        count_query = count_query.where(visibility)
    elif viewable_branch_ids is not None:
        allowed = set(viewable_branch_ids)
        if viewer_branch_id is not None:
            allowed.add(viewer_branch_id)
        visibility = or_(Post.branch_id.is_(None), Post.branch_id.in_(allowed))
        query = query.where(visibility)
        count_query = count_query.where(visibility)
    # else: no filter — sees everything

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
    period_start: date | None = None,
    period_end: date | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Post], int]:
    """Same visibility rule as get_posts, applied to one author's posts.

    period_start/period_end (both inclusive, calendar dates) optionally
    restrict to posts created within that window — used for "posts this
    week/period" stats. period_end's upper bound is treated as end-of-day
    so a same-day start/end window still matches posts created any time
    that day.
    """
    conditions = [Post.organization_id == organization_id, Post.user_id == user_id]

    if viewable_branch_ids is not None:
        allowed = set(viewable_branch_ids)
        if viewer_branch_id is not None:
            allowed.add(viewer_branch_id)
        conditions.append(or_(Post.branch_id.is_(None), Post.branch_id.in_(allowed)))

    if period_start is not None:
        conditions.append(Post.created_at >= datetime.combine(period_start, dt_time.min))
    if period_end is not None:
        conditions.append(Post.created_at <= datetime.combine(period_end, dt_time.max))

    query = select(Post).where(*conditions).order_by(Post.created_at.desc()).offset(offset).limit(limit)
    count_query = select(func.count()).select_from(Post).where(*conditions)

    total = (await session.execute(count_query)).scalar_one()
    posts = (await session.execute(query)).scalars().all()
    return list(posts), total