"""
app/services/leaderboard/get_leaderboard.py

Read-only — LeaderboardSnapshot rows are written exclusively by
compute_leaderboard_snapshots (app/jobs/analytics/leaderboard.py).
Latest period per (org/branch, metric_type, period_type) is resolved
by taking the most recent period_start on record — no "current period"
concept is assumed here, since job cadence could drift.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeaderboardSnapshot, User


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
        return [], None, None

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
        return [], None, None

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