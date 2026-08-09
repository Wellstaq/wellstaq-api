"""
app/services/leaderboard/get_leaderboard.py

Read-only — LeaderboardSnapshot rows are written exclusively by
compute_leaderboard_snapshots (app/jobs/analytics/leaderboard.py).

Latest period per (org/branch, metric_type, period_type) is resolved
by taking the most recent period_start on record — no "current period"
concept is assumed for row lookup, since job cadence could drift.

period_start/period_end returned to callers are always populated,
even when no snapshot has been computed yet: in that case we fall
back to the current calendar period's bounds (computed independently
of any row), so an empty leaderboard is a normal 200 with items: []
rather than an absent/undefined period.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeaderboardSnapshot, User


def compute_period_bounds(period_type: str, *, today: date | None = None) -> tuple[date, date]:
    """
    Pure function: current period's [start, end] for period_type, based on
    today's date. Independent of whether any snapshot rows exist — used as
    the fallback when no job run has produced data yet.
    """
    today = today or date.today()

    if period_type == "daily":
        return today, today

    if period_type == "weekly":
        start = today - timedelta(days=today.weekday())  # Monday
        end = start + timedelta(days=6)  # Sunday
        return start, end

    if period_type == "monthly":
        start = today.replace(day=1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        end = today.replace(day=last_day)
        return start, end

    raise ValueError(f"Unknown period_type: {period_type!r}")


async def _latest_period(session: AsyncSession, *, organization_id: str, metric_type: str, period_type: str):
    result = await session.execute(
        select(LeaderboardSnapshot.period_start, LeaderboardSnapshot.period_end)
        .where(
            LeaderboardSnapshot.organization_id == organization_id,
            LeaderboardSnapshot.metric_type == metric_type,
            LeaderboardSnapshot.period_type == period_type,
        )
        .order_by(LeaderboardSnapshot.period_start.desc())
        .limit(1)
    )
    return result.first()


async def get_branch_leaderboard(
    *, session: AsyncSession, organization_id: str, branch_id: str, metric_type: str, period_type: str, limit: int = 50
):
    latest = await _latest_period(session, organization_id=organization_id, metric_type=metric_type, period_type=period_type)
    if latest is None:
        period_start, period_end = compute_period_bounds(period_type)
        return [], period_start, period_end

    period_start, period_end = latest
    result = await session.execute(
        select(LeaderboardSnapshot, User)
        .join(User, User.id == LeaderboardSnapshot.user_id)
        .where(
            LeaderboardSnapshot.organization_id == organization_id,
            LeaderboardSnapshot.branch_id == branch_id,
            LeaderboardSnapshot.metric_type == metric_type,
            LeaderboardSnapshot.period_type == period_type,
            LeaderboardSnapshot.period_start == period_start,
        )
        .order_by(LeaderboardSnapshot.rank.asc())
        .limit(limit)
    )
    return list(result.all()), period_start, period_end


async def get_org_leaderboard(
    *, session: AsyncSession, organization_id: str, metric_type: str, period_type: str, limit: int = 50
):
    latest = await _latest_period(session, organization_id=organization_id, metric_type=metric_type, period_type=period_type)
    if latest is None:
        period_start, period_end = compute_period_bounds(period_type)
        return [], period_start, period_end

    period_start, period_end = latest
    result = await session.execute(
        select(LeaderboardSnapshot, User)
        .join(User, User.id == LeaderboardSnapshot.user_id)
        .where(
            LeaderboardSnapshot.organization_id == organization_id,
            LeaderboardSnapshot.metric_type == metric_type,
            LeaderboardSnapshot.period_type == period_type,
            LeaderboardSnapshot.period_start == period_start,
        )
        .order_by(LeaderboardSnapshot.org_rank.asc())
        .limit(limit)
    )
    return list(result.all()), period_start, period_end