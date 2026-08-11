"""
app/services/department_rank/get_department_rank.py

Read-only — DepartmentRankSnapshot rows are written exclusively by
compute_department_rank_snapshots (trailing 7-day window, ending
yesterday; see app/jobs/analytics/department_rank.py).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department, DepartmentRankSnapshot


async def get_organization_department_ranks(
    *, session: AsyncSession, organization_id: str, branch_id: str | None = None
):
    """Latest period's ranking for every department in the org, ordered
    by rank — the "Department Performance" screen's primary query."""
    latest_query = (
        select(DepartmentRankSnapshot.period_start, DepartmentRankSnapshot.period_end)
        .join(Department, Department.id == DepartmentRankSnapshot.department_id)
        .where(Department.organization_id == organization_id)
        .order_by(DepartmentRankSnapshot.period_start.desc())
        .limit(1)
    )
    if branch_id is not None:
        latest_query = latest_query.where(Department.branch_id == branch_id)
    latest = await session.execute(latest_query)
    latest_row = latest.first()
    if latest_row is None:
        return []

    period_start, period_end = latest_row
    rank_query = (
        select(DepartmentRankSnapshot, Department)
        .join(Department, Department.id == DepartmentRankSnapshot.department_id)
        .where(
            Department.organization_id == organization_id,
            DepartmentRankSnapshot.period_start == period_start,
            DepartmentRankSnapshot.period_end == period_end,
        )
        .order_by(DepartmentRankSnapshot.rank.asc())
    )
    if branch_id is not None:
        rank_query = rank_query.where(Department.branch_id == branch_id)
    result = await session.execute(rank_query)
    return list(result.all())


async def get_department_rank_history(*, session: AsyncSession, department_id: str, limit: int = 30):
    """A single department's snapshots over time, most recent first."""
    result = await session.execute(
        select(DepartmentRankSnapshot)
        .where(DepartmentRankSnapshot.department_id == department_id)
        .order_by(DepartmentRankSnapshot.period_start.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_department_latest_rank(*, session: AsyncSession, department_id: str) -> DepartmentRankSnapshot | None:
    """Latest single snapshot for one department, or None if it hasn't
    been ranked yet (e.g. no ActivityLog data in any 7-day window so
    far). Callers should render None fields as "not ranked yet" rather
    than treating this as an error — the department itself may still
    exist and be perfectly valid."""
    result = await session.execute(
        select(DepartmentRankSnapshot)
        .where(DepartmentRankSnapshot.department_id == department_id)
        .order_by(DepartmentRankSnapshot.period_start.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
