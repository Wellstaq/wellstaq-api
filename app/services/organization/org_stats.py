from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Challenge, Club, Department, Event, Post, User


PERIOD_DAYS = {"week": 7, "month": 30, "three_months": 91, "six_months": 182, "nine_months": 273, "year": 365}
DateWindow = tuple[datetime, datetime, datetime]


def _resolve_window(period: str | None, start_date: date | None, end_date: date | None) -> DateWindow | None:
    """Return current start/end and previous start; None means all-time."""
    if period is None:
        return None
    current_end_date = end_date or date.today()
    if period == "custom":
        if start_date is None:
            return None
        current_start_date = start_date
    else:
        days = PERIOD_DAYS.get(period, 30)
        current_start_date = current_end_date - timedelta(days=days - 1)

    current_start = datetime.combine(current_start_date, datetime.min.time(), tzinfo=timezone.utc)
    current_end = datetime.combine(current_end_date, datetime.max.time(), tzinfo=timezone.utc)
    duration = current_end_date - current_start_date + timedelta(days=1)
    previous_start = current_start - duration
    return current_start, current_end, previous_start


async def _count(session: AsyncSession, model, filters: list) -> int:
    return (await session.execute(
        select(func.count()).select_from(model).where(*filters)
    )).scalar_one()


def _pct_change(current: int, previous: int) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


async def _growth_trend(session: AsyncSession, model, base_filters: list, created_at_col, window: DateWindow | None) -> dict:
    """Records created in the selected window versus the preceding window.

    ``window=None`` represents Overall and therefore returns the all-time
    total with no comparison. A selected dashboard period must change both the
    displayed count and its comparison, not only the trend badge.
    """
    if window is None:
        current_total = await _count(session, model, base_filters)
        return {"current": current_total, "previous": None, "change_pct": None}

    current_start, current_end, previous_start = window
    current_total = await _count(
        session, model, base_filters + [created_at_col >= current_start, created_at_col <= current_end]
    )
    prior_total = await _count(
        session, model, base_filters + [created_at_col >= previous_start, created_at_col < current_start]
    )
    return {"current": current_total, "previous": prior_total, "change_pct": _pct_change(current_total, prior_total)}


async def _activity_trend(session: AsyncSession, model, base_filters: list, created_at_col, window: DateWindow | None) -> dict:
    """Pace trend: new records in the last window vs the window before that.
    window=None (Overall) -> just the all-time count, no comparison."""
    if window is None:
        current_count = await _count(session, model, base_filters)
        return {"current": current_count, "previous": None, "change_pct": None}

    current_start, current_end, previous_start = window
    current_count = await _count(session, model, base_filters + [created_at_col >= current_start, created_at_col <= current_end])
    prior_count = await _count(session, model, base_filters + [created_at_col >= previous_start, created_at_col < current_start])
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
    window = _resolve_window(period, start_date, end_date)

    user_filters = [User.organization_id == organization_id]
    if user_status is not None:
        user_filters.append(User.status == user_status)
    users_trend = await _growth_trend(session, User, user_filters, User.created_at, window)
    total_users = users_trend["current"]

    branch_filters = [Branch.organization_id == organization_id]
    branches_trend = await _growth_trend(session, Branch, branch_filters, Branch.created_at, window)
    total_branches = branches_trend["current"]

    dept_filters = [Department.organization_id == organization_id]
    departments_trend = await _growth_trend(session, Department, dept_filters, Department.created_at, window)
    total_departments = departments_trend["current"]

    club_filters = [Club.organization_id == organization_id]
    clubs_trend = await _growth_trend(session, Club, club_filters, Club.created_at, window)
    total_clubs = clubs_trend["current"]

    event_filters = [Event.organization_id == organization_id]
    if event_status is not None:
        event_filters.append(Event.status == event_status)
    events_trend = await _activity_trend(session, Event, event_filters, Event.created_at, window)
    total_events = events_trend["current"]

    challenge_filters = [Challenge.organization_id == organization_id]
    if challenge_status is not None:
        challenge_filters.append(Challenge.status == challenge_status)
    challenges_trend = await _activity_trend(session, Challenge, challenge_filters, Challenge.created_at, window)
    total_challenges = challenges_trend["current"]

    active_challenges = await _count(
        session, Challenge,
        [Challenge.organization_id == organization_id, Challenge.status == "active"],
    )

    post_filters = [Post.organization_id == organization_id]
    posts_trend = await _activity_trend(session, Post, post_filters, Post.created_at, window)
    total_posts = posts_trend["current"]

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
    window = _resolve_window(period, start_date, end_date)

    user_filters = [User.organization_id == organization_id, User.branch_id == branch_id]
    if user_status is not None:
        user_filters.append(User.status == user_status)
    users_trend = await _growth_trend(session, User, user_filters, User.created_at, window)
    total_users = users_trend["current"]

    dept_filters = [Department.organization_id == organization_id, Department.branch_id == branch_id]
    departments_trend = await _growth_trend(session, Department, dept_filters, Department.created_at, window)
    total_departments = departments_trend["current"]

    club_filters = [Club.organization_id == organization_id, Club.branch_id == branch_id]
    clubs_trend = await _growth_trend(session, Club, club_filters, Club.created_at, window)
    total_clubs = clubs_trend["current"]

    event_filters = [Event.organization_id == organization_id, Event.branch_id == branch_id]
    if event_status is not None:
        event_filters.append(Event.status == event_status)
    events_trend = await _activity_trend(session, Event, event_filters, Event.created_at, window)
    total_events = events_trend["current"]

    challenge_filters = [Challenge.organization_id == organization_id, Challenge.branch_id == branch_id]
    if challenge_status is not None:
        challenge_filters.append(Challenge.status == challenge_status)
    challenges_trend = await _activity_trend(session, Challenge, challenge_filters, Challenge.created_at, window)
    total_challenges = challenges_trend["current"]

    return {
        "total_users": total_users, "users_trend": users_trend,
        "total_departments": total_departments, "departments_trend": departments_trend,
        "total_clubs": total_clubs, "clubs_trend": clubs_trend,
        "total_events": total_events, "events_trend": events_trend,
        "total_challenges": total_challenges, "challenges_trend": challenges_trend,
    }
