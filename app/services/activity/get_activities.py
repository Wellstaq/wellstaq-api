"""
app/services/activity/get_activities.py
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserActivityFeed

PERIOD_DAYS = {"week": 7, "month": 30, "three_months": 91, "six_months": 182, "nine_months": 273, "year": 365}


def _window(period: str | None, start_date: date | None, end_date: date | None):
    if period is None:
        return None
    now = datetime.now(timezone.utc)
    if period == "custom" and start_date:
        start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end = datetime.combine(end_date, time.max, tzinfo=timezone.utc) if end_date else now
        duration = max(timedelta(days=1), end - start)
        return start, end, start - duration
    duration = timedelta(days=PERIOD_DAYS.get(period, 30))
    return now - duration, now, now - (duration * 2)


async def get_activity_trend(*, session: AsyncSession, organization_id: str, branch_id: str | None = None,
                             period: str | None = None, start_date: date | None = None,
                             end_date: date | None = None) -> dict | None:
    bounds = _window(period, start_date, end_date)
    if bounds is None:
        return None
    start, end, previous_start = bounds
    filters = [UserActivityFeed.organization_id == organization_id]
    query_from = UserActivityFeed
    if branch_id is not None:
        query_from = UserActivityFeed.__table__.join(User, User.id == UserActivityFeed.user_id)
        filters.append(User.branch_id == branch_id)

    async def count_between(lower, upper, include_upper: bool) -> int:
        upper_filter = UserActivityFeed.created_at <= upper if include_upper else UserActivityFeed.created_at < upper
        query = select(func.count()).select_from(query_from).where(*filters, UserActivityFeed.created_at >= lower, upper_filter)
        return (await session.execute(query)).scalar_one()

    current = await count_between(start, end, True)
    previous = await count_between(previous_start, start, False)
    change_pct = None if previous == 0 else round(((current - previous) / previous) * 100, 1)
    return {"current": current, "previous": previous, "change_pct": change_pct}


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
