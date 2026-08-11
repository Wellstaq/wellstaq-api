"""
app/services/kpi_snapshot/kpi_snapshot_service.py

Read-only from the service layer's perspective for callers — rows are
written exclusively by the backend scheduler (compute_kpi_snapshots
job), never by a user-facing route. See KPISnapshot's model docstring.

Org-wide figures are always derived at read time — SUM(value) GROUP BY
period_start across every branch's rows for the same organization_id/
metric_name — never stored as their own row, matching Target's own
org-wide rollup pattern.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshot_target import KPISnapshot
from app.services.kpi_snapshot._helpers import validate_branch


async def list_branch_kpi_snapshots(
    *, session: AsyncSession, organization_id: str, branch_id: str, metric_name: str,
    period_start: date, period_end: date,
) -> list[KPISnapshot]:
    await validate_branch(session=session, organization_id=organization_id, branch_id=branch_id)

    normalized_metric_name = metric_name.strip().lower()
    result = await session.execute(
        select(KPISnapshot)
        .where(
            KPISnapshot.organization_id == organization_id,
            KPISnapshot.branch_id == branch_id,
            KPISnapshot.metric_name == normalized_metric_name,
            KPISnapshot.period_start >= period_start,
            KPISnapshot.period_start <= period_end,
        )
        .order_by(KPISnapshot.period_start.asc())
    )
    return list(result.scalars().all())


async def get_org_wide_kpi_snapshots(
    *, session: AsyncSession, organization_id: str, metric_name: str, period_start: date, period_end: date,
) -> list[dict]:
    """Derived rollup — SUM(value) per period_start across every branch
    in the org that has a snapshot for that date. Not stored."""
    normalized_metric_name = metric_name.strip().lower()

    result = await session.execute(
        select(
            KPISnapshot.period_start,
            KPISnapshot.period_end,
            func.sum(KPISnapshot.value),
            func.count(KPISnapshot.id),
        )
        .where(
            KPISnapshot.organization_id == organization_id,
            KPISnapshot.metric_name == normalized_metric_name,
            KPISnapshot.period_start >= period_start,
            KPISnapshot.period_start <= period_end,
        )
        .group_by(KPISnapshot.period_start, KPISnapshot.period_end)
        .order_by(KPISnapshot.period_start.asc())
    )
    rows = result.all()
    return [
        {"period_start": ps, "period_end": pe, "value": total, "branch_count": count}
        for ps, pe, total, count in rows
    ]