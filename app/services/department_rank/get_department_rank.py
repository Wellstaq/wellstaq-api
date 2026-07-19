"""
app/services/department_rank/get_department_rank.py

Read-only — DepartmentRankSnapshot rows are written exclusively by
compute_department_rank_snapshots.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Department, DepartmentRankSnapshot


async def get_organization_department_ranks(*, session: AsyncSession, organization_id: str):
    """Latest period's ranking for every department in the org, ordered
    by rank — the "Department Performance" screen's primary query."""
    latest = await session.execute(
        select(DepartmentRankSnapshot.period_start, DepartmentRankSnapshot.period_end)
        .join(Department, Department.id == DepartmentRankSnapshot.department_id)
        .where(Department.organization_id == organization_id)
        .order_by(DepartmentRankSnapshot.period_start.desc())
        .limit(1)
    )
    latest_row = latest.first()
    if latest_row is None:
        return []

    period_start, period_end = latest_row
    result = await session.execute(
        select(DepartmentRankSnapshot, Department)
        .join(Department, Department.id == DepartmentRankSnapshot.department_id)
        .where(
            Department.organization_id == organization_id,
            DepartmentRankSnapshot.period_start == period_start,
            DepartmentRankSnapshot.period_end == period_end,
        )
        .order_by(DepartmentRankSnapshot.rank.asc())
    )
    return list(result.all())


async def get_department_rank_history(*, session: AsyncSession, department_id: str, limit: int = 30):
    result = await session.execute(
        select(DepartmentRankSnapshot)
        .where(DepartmentRankSnapshot.department_id == department_id)
        .order_by(DepartmentRankSnapshot.period_start.desc())
        .limit(limit)
    )
    return list(result.scalars().all())