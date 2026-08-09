from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Challenge, Club, Department, Event, Post, User


PERIOD_DAYS = {"week": 7, "month": 30, "three_months": 91, "six_months": 182, "nine_months": 273, "year": 365}

def _resolve_window_days(period: str | None, start_date: date | None, end_date: date | None) -> int | None:
    """None = overall/all-time — no trend window, current vs nothing."""
    if period is None:
        return None
    if period == "custom":
        if start_date is None:
            return None
        end = end_date or date.today()
        return max(1, (end - start_date).days)
    return PERIOD_DAYS.get(period, 30)


async def _count(session: AsyncSession, model, filters: list) -> int:
    return (await session.execute(
        select(func.count()).select_from(model).where(*filters)
    )).scalar_one()


def _pct_change(current: int, previous: int) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


async def _growth_trend(session: AsyncSession, model, base_filters: list, created_at_col, window_days: int | None) -> dict:
    """Cumulative growth trend: total now vs total as of `window_days` ago.
    window_days=None (Overall) -> no baseline to diff against, trend is null."""
    if window_days is None:
        current_total = await _count(session, model, base_filters)
        return {"current": current_total, "previous": None, "change_pct": None}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    current_total = await _count(session, model, base_filters)
    prior_total = await _count(session, model, base_filters + [created_at_col <= cutoff])
    return {"current": current_total, "previous": prior_total, "change_pct": _pct_change(current_total, prior_total)}


async def _activity_trend(session: AsyncSession, model, base_filters: list, created_at_col, window_days: int | None) -> dict:
    """Pace trend: new records in the last window vs the window before that.
    window_days=None (Overall) -> just the all-time count, no comparison."""
    if window_days is None:
        current_count = await _count(session, model, base_filters)
        return {"current": current_count, "previous": None, "change_pct": None}

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)
    prior_window_start = now - timedelta(days=window_days * 2)
    current_count = await _count(session, model, base_filters + [created_at_col >= window_start, created_at_col <= now])
    prior_count = await _count(session, model, base_filters + [created_at_col >= prior_window_start, created_at_col < window_start])
    return {"current": current_count, "previous": prior_count, "change_pct": _pct_change(current_count, prior_count)}


async def get_org_stats(
    *,
    session: AsyncSession,
    organization_id: str,
    user_status: str | None = None,
    challenge_status: str | None = None,
    event_status: str | None = None,
    period: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    window_days = _resolve_window_days(period, start_date, end_date)

    user_filters = [User.organization_id == organization_id]
    if user_status is not None:
        user_filters.append(User.status == user_status)
    total_users = await _count(session, User, user_filters)
    users_trend = await _growth_trend(session, User, user_filters, User.created_at, window_days)

    branch_filters = [Branch.organization_id == organization_id]
    total_branches = await _count(session, Branch, branch_filters)
    branches_trend = await _growth_trend(session, Branch, branch_filters, Branch.created_at, window_days)

    dept_filters = [Department.organization_id == organization_id]
    total_departments = await _count(session, Department, dept_filters)
    departments_trend = await _growth_trend(session, Department, dept_filters, Department.created_at, window_days)

    club_filters = [Club.organization_id == organization_id]
    total_clubs = await _count(session, Club, club_filters)
    clubs_trend = await _growth_trend(session, Club, club_filters, Club.created_at, window_days)

    event_filters = [Event.organization_id == organization_id]
    if event_status is not None:
        event_filters.append(Event.status == event_status)
    total_events = await _count(session, Event, event_filters)
    events_trend = await _activity_trend(session, Event, event_filters, Event.created_at, window_days)

    challenge_filters = [Challenge.organization_id == organization_id]
    if challenge_status is not None:
        challenge_filters.append(Challenge.status == challenge_status)
    total_challenges = await _count(session, Challenge, challenge_filters)
    challenges_trend = await _activity_trend(session, Challenge, challenge_filters, Challenge.created_at, window_days)

    active_challenges = await _count(
        session, Challenge,
        [Challenge.organization_id == organization_id, Challenge.status == "active"],
    )

    post_filters = [Post.organization_id == organization_id]
    total_posts = await _count(session, Post, post_filters)
    posts_trend = await _activity_trend(session, Post, post_filters, Post.created_at, window_days)

    return {
        "total_users": total_users, "users_trend": users_trend,
        "total_branches": total_branches, "branches_trend": branches_trend,
        "total_departments": total_departments, "departments_trend": departments_trend,
        "total_clubs": total_clubs, "clubs_trend": clubs_trend,
        "total_events": total_events, "events_trend": events_trend,
        "total_challenges": total_challenges, "challenges_trend": challenges_trend,
        "active_challenges": active_challenges,
        "total_posts": total_posts, "posts_trend": posts_trend,
    }


async def get_branch_stats(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    user_status: str | None = None,
    challenge_status: str | None = None,
    event_status: str | None = None,
    period: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    window_days = _resolve_window_days(period, start_date, end_date)

    user_filters = [User.organization_id == organization_id, User.branch_id == branch_id]
    if user_status is not None:
        user_filters.append(User.status == user_status)
    total_users = await _count(session, User, user_filters)
    users_trend = await _growth_trend(session, User, user_filters, User.created_at, window_days)

    dept_filters = [Department.organization_id == organization_id, Department.branch_id == branch_id]
    total_departments = await _count(session, Department, dept_filters)
    departments_trend = await _growth_trend(session, Department, dept_filters, Department.created_at, window_days)

    club_filters = [Club.organization_id == organization_id, Club.branch_id == branch_id]
    total_clubs = await _count(session, Club, club_filters)
    clubs_trend = await _growth_trend(session, Club, club_filters, Club.created_at, window_days)

    event_filters = [Event.organization_id == organization_id, Event.branch_id == branch_id]
    if event_status is not None:
        event_filters.append(Event.status == event_status)
    total_events = await _count(session, Event, event_filters)
    events_trend = await _activity_trend(session, Event, event_filters, Event.created_at, window_days)

    challenge_filters = [Challenge.organization_id == organization_id, Challenge.branch_id == branch_id]
    if challenge_status is not None:
        challenge_filters.append(Challenge.status == challenge_status)
    total_challenges = await _count(session, Challenge, challenge_filters)
    challenges_trend = await _activity_trend(session, Challenge, challenge_filters, Challenge.created_at, window_days)

    return {
        "total_users": total_users, "users_trend": users_trend,
        "total_departments": total_departments, "departments_trend": departments_trend,
        "total_clubs": total_clubs, "clubs_trend": clubs_trend,
        "total_events": total_events, "events_trend": events_trend,
        "total_challenges": total_challenges, "challenges_trend": challenges_trend,
    }