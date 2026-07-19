"""
app/services/activity/get_activities.py
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserActivityFeed


async def get_user_activities(
    *, session: AsyncSession, organization_id: str, user_id: str, offset: int = 0, limit: int = 50
) -> tuple[list[UserActivityFeed], int]:
    """Raw feed — caller must ensure this is only used for self-viewing;
    the router is responsible for that access check, not this function."""
    query = (
        select(UserActivityFeed)
        .where(UserActivityFeed.organization_id == organization_id, UserActivityFeed.user_id == user_id)
        .order_by(UserActivityFeed.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    count_query = select(func.count()).select_from(UserActivityFeed).where(
        UserActivityFeed.organization_id == organization_id, UserActivityFeed.user_id == user_id
    )

    total = (await session.execute(count_query)).scalar_one()
    activities = (await session.execute(query)).scalars().all()
    return list(activities), total


async def get_user_activity_count(*, session: AsyncSession, organization_id: str, user_id: str) -> int:
    query = select(func.count()).select_from(UserActivityFeed).where(
        UserActivityFeed.organization_id == organization_id, UserActivityFeed.user_id == user_id
    )
    return (await session.execute(query)).scalar_one()


async def get_organization_activity_summary(
    *, session: AsyncSession, organization_id: str
) -> tuple[int, list[tuple[str, int]]]:
    """Aggregate only — counts grouped by activity_type. No raw entries
    ever leave this function."""
    total_query = select(func.count()).select_from(UserActivityFeed).where(
        UserActivityFeed.organization_id == organization_id
    )
    by_type_query = (
        select(UserActivityFeed.activity_type, func.count())
        .where(UserActivityFeed.organization_id == organization_id)
        .group_by(UserActivityFeed.activity_type)
        .order_by(func.count().desc())
    )

    total = (await session.execute(total_query)).scalar_one()
    by_type_rows = (await session.execute(by_type_query)).all()
    return total, list(by_type_rows)


async def get_branch_activity_summary(
    *, session: AsyncSession, organization_id: str, branch_id: str
) -> tuple[int, list[tuple[str, int]]]:
    """Same aggregate-only guarantee as get_organization_activity_summary,
    scoped to one branch via a join through User.branch_id — UserActivityFeed
    itself has no branch_id column."""
    total_query = (
        select(func.count())
        .select_from(UserActivityFeed)
        .join(User, User.id == UserActivityFeed.user_id)
        .where(UserActivityFeed.organization_id == organization_id, User.branch_id == branch_id)
    )
    by_type_query = (
        select(UserActivityFeed.activity_type, func.count())
        .join(User, User.id == UserActivityFeed.user_id)
        .where(UserActivityFeed.organization_id == organization_id, User.branch_id == branch_id)
        .group_by(UserActivityFeed.activity_type)
        .order_by(func.count().desc())
    )

    total = (await session.execute(total_query)).scalar_one()
    by_type_rows = (await session.execute(by_type_query)).all()
    return total, list(by_type_rows)