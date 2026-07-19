"""
app/jobs/analytics/leaderboard.py

compute_leaderboard_snapshots recomputes LeaderboardSnapshot rows for
every organization and metric_type with activity in the requested
period, computing both branch-local and organization-wide rankings.

period_type ('daily' | 'weekly' | 'monthly') distinguishes snapshot
granularity so identical date ranges cannot be confused between
different reporting periods. The same function can therefore be reused
for daily, weekly, monthly, or manual backfill jobs.

The job is idempotent: existing snapshots for the same
(organization_id, user_id, metric_type, period_type, period_start,
period_end) are updated instead of duplicated, assuming the model
enforces the corresponding UniqueConstraint.

Performance: all reads are bulk queries (no per-user database queries).
Ranking is performed entirely in memory. Remaining tradeoffs are the
per-organization/per-metric iteration and one ORM write per snapshot
row, both acceptable until larger production scale requires further
optimization.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any
from sqlalchemy import func, select

from app.core.database import get_db
from app.models import ActivityLog, LeaderboardSnapshot, User

logger = logging.getLogger(__name__)


async def compute_leaderboard_snapshots(
    ctx: dict[str, Any],
    period_type: str = "daily",
    period_start: date | None = None,
    period_end: date | None = None,
) -> None:
    if period_type not in ("daily", "weekly", "monthly"):
        raise ValueError(f"Invalid period_type '{period_type}'.")

    if period_end is None:
        period_end = date.today() - timedelta(days=1)
    if period_start is None:
        window_days = {"daily": 1, "weekly": 7, "monthly": 30}[period_type]
        period_start = period_end - timedelta(days=window_days - 1)

    written = 0

    async for session in get_db():
        organization_ids = await _load_organization_ids(session)

        for organization_id in organization_ids:
            metric_types = await _load_metric_types_in_period(session, organization_id, period_start, period_end)

            for metric_type in metric_types:
                user_values = await _load_user_totals(session, organization_id, metric_type, period_start, period_end)
                if not user_values:
                    continue

                user_branches = await _load_user_branches(session, list(user_values.keys()))
                previous_ranks = await _load_previous_ranks(session, organization_id, metric_type, period_type, period_start)
                existing_rows = await _load_existing_rows(session, organization_id, metric_type, period_type, period_start, period_end)

                written += await _write_snapshots(
                    session=session, organization_id=organization_id, metric_type=metric_type, period_type=period_type,
                    period_start=period_start, period_end=period_end,
                    user_values=user_values, user_branches=user_branches,
                    previous_ranks=previous_ranks, existing_rows=existing_rows,
                )

        await session.commit()
        break

    logger.info(
        "Leaderboard snapshots computed",
        extra={"period_type": period_type, "period_start": str(period_start), "period_end": str(period_end), "rows_written": written},
    )


# ── Bulk loaders ─────────────────────────────────────────────────────────────

async def _load_organization_ids(session) -> list[str]:
    result = await session.execute(select(User.organization_id).distinct())
    return [row[0] for row in result.all()]


async def _load_metric_types_in_period(session, organization_id: str, period_start: date, period_end: date) -> list[str]:
    result = await session.execute(
        select(ActivityLog.metric_type)
        .where(
            ActivityLog.organization_id == organization_id,
            ActivityLog.date >= period_start, ActivityLog.date <= period_end,
        )
        .distinct()
    )
    return [row[0] for row in result.all()]


async def _load_user_totals(session, organization_id: str, metric_type: str, period_start: date, period_end: date) -> dict[str, float]:
    result = await session.execute(
        select(ActivityLog.user_id, func.sum(ActivityLog.value))
        .where(
            ActivityLog.organization_id == organization_id, ActivityLog.metric_type == metric_type,
            ActivityLog.date >= period_start, ActivityLog.date <= period_end,
        )
        .group_by(ActivityLog.user_id)
    )
    return {user_id: float(total) for user_id, total in result.all()}


async def _load_user_branches(session, user_ids: list[str]) -> dict[str, str]:
    result = await session.execute(select(User.id, User.branch_id).where(User.id.in_(user_ids)))
    return {user_id: branch_id for user_id, branch_id in result.all()}


async def _load_previous_ranks(session, organization_id: str, metric_type: str, period_type: str, period_start: date) -> dict[str, tuple[int, int]]:
    """
    Now filtered by period_type too — a weekly run only ever compares
    against a PRIOR WEEKLY snapshot, never a monthly or daily one. This
    is the fix for the exact bug flagged during review: comparing
    non-comparable period types would previously have been possible.
    """
    result = await session.execute(
        select(LeaderboardSnapshot.user_id, LeaderboardSnapshot.rank, LeaderboardSnapshot.org_rank)
        .where(
            LeaderboardSnapshot.organization_id == organization_id,
            LeaderboardSnapshot.metric_type == metric_type,
            LeaderboardSnapshot.period_type == period_type,
            LeaderboardSnapshot.period_start < period_start,
        )
        .order_by(LeaderboardSnapshot.period_start.desc())
    )
    previous: dict[str, tuple[int, int]] = {}
    for user_id, rank, org_rank in result.all():
        if user_id not in previous:
            previous[user_id] = (rank, org_rank)
    return previous


async def _load_existing_rows(
    session, organization_id: str, metric_type: str, period_type: str, period_start: date, period_end: date
) -> dict[str, LeaderboardSnapshot]:
    """Bulk-loads any snapshot rows already written for THIS EXACT
    period (not prior periods) — used to update in place instead of
    inserting a duplicate. This is the idempotency fix: re-running the
    job for a period it already computed updates those same rows rather
    than adding new ones, backed by the UniqueConstraint on the model."""
    result = await session.execute(
        select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.organization_id == organization_id,
            LeaderboardSnapshot.metric_type == metric_type,
            LeaderboardSnapshot.period_type == period_type,
            LeaderboardSnapshot.period_start == period_start,
            LeaderboardSnapshot.period_end == period_end,
        )
    )
    rows = result.scalars().all()
    return {row.user_id: row for row in rows}


# ── Ranking + write ──────────────────────────────────────────────────────────

async def _write_snapshots(
    *, session, organization_id: str, metric_type: str, period_type: str, period_start: date, period_end: date,
    user_values: dict[str, float], user_branches: dict[str, str],
    previous_ranks: dict[str, tuple[int, int]], existing_rows: dict[str, LeaderboardSnapshot],
) -> int:
    org_sorted = sorted(user_values.items(), key=lambda item: item[1], reverse=True)
    org_ranks = {user_id: idx + 1 for idx, (user_id, _value) in enumerate(org_sorted)}

    by_branch: dict[str, list[tuple[str, float]]] = {}
    for user_id, value in user_values.items():
        branch_id = user_branches.get(user_id)
        if branch_id is None:
            continue
        by_branch.setdefault(branch_id, []).append((user_id, value))

    branch_ranks: dict[str, int] = {}
    branch_of: dict[str, str] = {}
    for branch_id, entries in by_branch.items():
        branch_sorted = sorted(entries, key=lambda item: item[1], reverse=True)
        for idx, (user_id, _value) in enumerate(branch_sorted):
            branch_ranks[user_id] = idx + 1
            branch_of[user_id] = branch_id

    written = 0
    for user_id, value in user_values.items():
        if user_id not in branch_ranks:
            continue

        previous_rank, previous_org_rank = previous_ranks.get(user_id, (None, None))
        rank = branch_ranks[user_id]
        org_rank = org_ranks[user_id]

        existing = existing_rows.get(user_id)
        if existing is not None:
            existing.value = value
            existing.rank = rank
            existing.previous_rank = previous_rank
            existing.org_rank = org_rank
            existing.previous_org_rank = previous_org_rank
        else:
            session.add(LeaderboardSnapshot(
                organization_id=organization_id, branch_id=branch_of[user_id], user_id=user_id,
                metric_type=metric_type, value=value, rank=rank, previous_rank=previous_rank,
                org_rank=org_rank, previous_org_rank=previous_org_rank,
                period_type=period_type, period_start=period_start, period_end=period_end,
            ))
        written += 1

    return written


# ── Cron entry points — ARQ's cron() needs a real function with a
# __qualname__, so functools.partial doesn't work here. These are thin
# wrappers, one per granularity, each just binding period_type. ──────────

async def compute_leaderboard_snapshots_daily(ctx: dict[str, Any]) -> None:
    await compute_leaderboard_snapshots(ctx, period_type="daily")


async def compute_leaderboard_snapshots_weekly(ctx: dict[str, Any]) -> None:
    await compute_leaderboard_snapshots(ctx, period_type="weekly")


async def compute_leaderboard_snapshots_monthly(ctx: dict[str, Any]) -> None:
    await compute_leaderboard_snapshots(ctx, period_type="monthly")