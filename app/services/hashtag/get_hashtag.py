"""
app/services/hashtag/get_hashtag.py
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hashtag, HashtagTrendSnapshot, Post, PostHashtag
from app.services.hashtag._helpers import normalize_tag


async def search_hashtags(
    *, session: AsyncSession, organization_id: str, query: str, offset: int = 0, limit: int = 20
) -> tuple[list[Hashtag], int]:
    """Prefix search on tag_name, ordered by popularity."""
    normalized = normalize_tag(query)

    base_query = (
        select(Hashtag)
        .where(Hashtag.organization_id == organization_id, Hashtag.tag_name.like(f"{normalized}%"))
        .order_by(Hashtag.total_post_count.desc())
        .offset(offset)
        .limit(limit)
    )
    count_query = (
        select(func.count())
        .select_from(Hashtag)
        .where(Hashtag.organization_id == organization_id, Hashtag.tag_name.like(f"{normalized}%"))
    )

    total = (await session.execute(count_query)).scalar_one()
    hashtags = (await session.execute(base_query)).scalars().all()
    return list(hashtags), total


async def get_trending_hashtags(
    *, session: AsyncSession, organization_id: str, limit: int = 20
) -> list[tuple[Hashtag, HashtagTrendSnapshot]]:
    """
    Returns the most recent trend snapshot per hashtag, ordered by score.

    TODO(background-job): this reads whatever HashtagTrendSnapshot rows
    exist. Until the scheduled trend-computation job is built (every 15/60
    min per the model docstring), this will return an empty list — there's
    nothing populating snapshots yet. Falling back to total_post_count
    ordering isn't done here deliberately, so this function's behavior
    doesn't silently change once the real job ships.
    """
    latest_window_subq = (
        select(
            HashtagTrendSnapshot.hashtag_id,
            func.max(HashtagTrendSnapshot.window_end).label("max_window_end"),
        )
        .where(HashtagTrendSnapshot.organization_id == organization_id)
        .group_by(HashtagTrendSnapshot.hashtag_id)
        .subquery()
    )

    query = (
        select(Hashtag, HashtagTrendSnapshot)
        .join(HashtagTrendSnapshot, HashtagTrendSnapshot.hashtag_id == Hashtag.id)
        .join(
            latest_window_subq,
            (HashtagTrendSnapshot.hashtag_id == latest_window_subq.c.hashtag_id)
            & (HashtagTrendSnapshot.window_end == latest_window_subq.c.max_window_end),
        )
        .where(Hashtag.organization_id == organization_id)
        .order_by(HashtagTrendSnapshot.trend_score.desc())
        .limit(limit)
    )
    result = await session.execute(query)
    return list(result.all())


async def get_posts_by_hashtag(
    *, session: AsyncSession, organization_id: str, tag_name: str, offset: int = 0, limit: int = 50
) -> tuple[list[Post], int]:
    normalized = normalize_tag(tag_name)

    query = (
        select(Post)
        .join(PostHashtag, PostHashtag.post_id == Post.id)
        .join(Hashtag, Hashtag.id == PostHashtag.hashtag_id)
        .where(Hashtag.organization_id == organization_id, Hashtag.tag_name == normalized)
        .order_by(Post.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    count_query = (
        select(func.count())
        .select_from(PostHashtag)
        .join(Hashtag, Hashtag.id == PostHashtag.hashtag_id)
        .where(Hashtag.organization_id == organization_id, Hashtag.tag_name == normalized)
    )

    total = (await session.execute(count_query)).scalar_one()
    posts = (await session.execute(query)).scalars().all()
    return list(posts), total