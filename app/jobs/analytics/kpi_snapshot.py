"""
app/jobs/analytics/kpi_snapshot.py

compute_kpi_snapshots — the only writer of KPISnapshot rows. Computes
one steps_total and one steps_active_users row per branch, once daily,
for the single day ending yesterday — matching the "no backdating"
guarantee on ActivityLog submissions (see submit_activity), so a given
calendar day's totals are final once that day has passed.

Org-wide figures are NOT stored — they're derived at read time by
summing every branch's row for the same organization_id/metric_name/
period_start (branches are disjoint, so SUM is correct — see
KPISnapshot's model docstring, same pattern Target already uses).

IDEMPOTENCY: existing rows for (branch_id, metric_name, period_start)
are bulk-loaded and updated in place rather than duplicated, same
pattern as engagement/department_rank/wellbeing_score.

PERFORMANCE DESIGN: one bulk query per metric (SUM + COUNT DISTINCT,
grouped by branch), not a per-branch loop of individual queries. Writes
are still one INSERT/UPDATE per row at flush time (same deferred cost
flagged in the other analytics jobs).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from app.jobs.shared import get_job_session
from app.models import ActivityLog, Branch, User
from app.models.snapshot_target import KPISnapshot

logger = logging.getLogger(__name__)

STEPS_TOTAL_METRIC = "steps_total"
STEPS_ACTIVE_USERS_METRIC = "steps_active_users"

# Extend as more metrics need charting support in the KPI overview.
SNAPSHOT_METRIC_TYPES = ("steps",)


async def compute_kpi_snapshots(ctx: dict[str, Any]) -> None:
    del ctx  # required by ARQ's call signature, unused here

    target_date = date.today() - timedelta(days=1)
    written = 0

    async with get_job_session() as session:
        branches = await _load_branches(session)
        if not branches:
            logger.info("KPI snapshots computed", extra={"target_date": str(target_date), "rows_written": 0})
            return

        for metric_type in SNAPSHOT_METRIC_TYPES:
            totals = await _load_metric_totals(session, metric_type, target_date)
            existing_rows = await _load_existing_rows(session, metric_type, target_date)

            for branch_id, organization_id in branches:
                total_value, active_count = totals.get(branch_id, (0.0, 0))

                written += _upsert_snapshot(
                    session=session, existing_rows=existing_rows,
                    organization_id=organization_id, branch_id=branch_id,
                    metric_name=f"{metric_type}_total", target_date=target_date, value=total_value,
                )
                written += _upsert_snapshot(
                    session=session, existing_rows=existing_rows,
                    organization_id=organization_id, branch_id=branch_id,
                    metric_name=f"{metric_type}_active_users", target_date=target_date, value=float(active_count),
                )

        await session.commit()

    logger.info("KPI snapshots computed", extra={"target_date": str(target_date), "rows_written": written})


async def _load_branches(session) -> list[tuple[str, str]]:
    result = await session.execute(select(Branch.id, Branch.organization_id))
    return list(result.all())


async def _load_metric_totals(session, metric_type: str, target_date: date) -> dict[str, tuple[float, int]]:
    """Returns {branch_id: (sum_value, distinct_active_user_count)} for
    one metric_type on target_date, across all branches in one query."""
    result = await session.execute(
        select(
            ActivityLog.branch_id,
            func.coalesce(func.sum(ActivityLog.value), 0.0),
            func.count(func.distinct(ActivityLog.user_id)),
        )
        .join(User, User.id == ActivityLog.user_id)
        .where(
            ActivityLog.metric_type == metric_type,
            ActivityLog.date == target_date,
            User.status == "active",
        )
        .group_by(ActivityLog.branch_id)
    )
    return {branch_id: (float(total), int(count)) for branch_id, total, count in result.all()}


async def _load_existing_rows(session, metric_type: str, target_date: date) -> dict[tuple[str, str], KPISnapshot]:
    result = await session.execute(
        select(KPISnapshot).where(
            KPISnapshot.metric_name.in_([f"{metric_type}_total", f"{metric_type}_active_users"]),
            KPISnapshot.period_start == target_date,
        )
    )
    rows = result.scalars().all()
    return {(row.branch_id, row.metric_name): row for row in rows}


def _upsert_snapshot(
    *, session, existing_rows: dict[tuple[str, str], KPISnapshot],
    organization_id: str, branch_id: str, metric_name: str, target_date: date, value: float,
) -> int:
    row = existing_rows.get((branch_id, metric_name))
    if row is not None:
        row.value = value
        row.period_end = target_date
    else:
        session.add(KPISnapshot(
            organization_id=organization_id, branch_id=branch_id, metric_name=metric_name,
            period_start=target_date, period_end=target_date, value=value,
        ))
    return 1
