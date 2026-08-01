"""
app/jobs/analytics/department_rank.py

compute_department_rank_snapshots — recomputes DepartmentRankSnapshot
rows for every department, across every organization, once daily (see
worker.py cron). Scores the trailing 7 days ending yesterday, same
windowing convention as engagement and leaderboard.

Per-department stats:
  total_activities: count of ActivityLog rows logged by any member of
                     the department in the window.
  avg_daily_steps:  average of the department's members' steps-metric
                     ActivityLog values over the window (0 if no steps
                     data at all — not None, so departments with zero
                     step-logging still rank, just at the bottom).
  performance_score: a normalized 0-100 blend of the two stats above,
                     relative to the highest-performing department in
                     the SAME organization that period (i.e. the best
                     department that period scores ~100, others scored
                     relative to it). This is a first-pass formula, not
                     validated against real usage — see TODO below.
  rank:              the department's position among its org's other
                     departments, ordered by performance_score
                     descending.

Department membership: a user's department is read via DepartmentMember
(one department per user, per that model's UniqueConstraint), NOT a
department_id column directly on User — there isn't one.

IDEMPOTENCY: existing rows for the exact (department_id, period_start,
period_end) are bulk-loaded and updated in place rather than
duplicated, same pattern as the leaderboard job.

PERFORMANCE DESIGN: reads are bulk GROUP BY queries — one to map users
to departments, one for total_activities per department, one for
avg_daily_steps per department. No per-department, per-user loop for
the read side. Writes are still one INSERT/UPDATE per row at flush
time (same deferred cost flagged for engagement/leaderboard).

TODO(formula-review): performance_score's relative-to-best-department
formula is a first-pass placeholder. Revisit once there's real usage
data to validate against, or a clearer product opinion on what
"department performance" should weigh (e.g. participation consistency
like engagement uses, not just raw totals).
"""


from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from app.jobs.shared import get_job_session
from app.models import ActivityLog, Department, DepartmentMember, DepartmentRankSnapshot

logger = logging.getLogger(__name__)

STEPS_METRIC_TYPE = "steps"


async def compute_department_rank_snapshots(ctx: dict[str, Any]) -> None:
    period_end = date.today() - timedelta(days=1)
    period_start = period_end - timedelta(days=6)

    written = 0

    async with get_job_session() as session:
        organization_ids = await _load_organization_ids(session)

        for organization_id in organization_ids:
            department_ids = await _load_department_ids(session, organization_id)
            if not department_ids:
                continue

            user_to_department = await _load_user_department_map(session, department_ids)
            total_activities = await _load_total_activities(session, list(user_to_department.keys()), period_start, period_end)
            avg_steps = await _load_avg_steps(session, list(user_to_department.keys()), period_start, period_end)
            existing_rows = await _load_existing_rows(session, department_ids, period_start, period_end)

            written += await _write_snapshots(
                session=session, organization_id=organization_id, department_ids=department_ids,
                user_to_department=user_to_department, total_activities=total_activities, avg_steps=avg_steps,
                period_start=period_start, period_end=period_end, existing_rows=existing_rows,
            )

        await session.commit()

    logger.info(
        "Department rank snapshots computed",
        extra={"period_start": str(period_start), "period_end": str(period_end), "rows_written": written},
    )


async def _load_organization_ids(session) -> list[str]:
    result = await session.execute(select(Department.organization_id).distinct())
    return [row[0] for row in result.all()]


async def _load_department_ids(session, organization_id: str) -> list[str]:
    result = await session.execute(select(Department.id).where(Department.organization_id == organization_id))
    return [row[0] for row in result.all()]


async def _load_user_department_map(session, department_ids: list[str]) -> dict[str, str]:
    result = await session.execute(
        select(DepartmentMember.user_id, DepartmentMember.department_id)
        .where(DepartmentMember.department_id.in_(department_ids))
    )
    return {user_id: department_id for user_id, department_id in result.all()}


async def _load_total_activities(session, user_ids: list[str], period_start: date, period_end: date) -> dict[str, int]:
    if not user_ids:
        return {}
    result = await session.execute(
        select(ActivityLog.user_id, func.count(ActivityLog.id))
        .where(
            ActivityLog.user_id.in_(user_ids),
            ActivityLog.date >= period_start, ActivityLog.date <= period_end,
        )
        .group_by(ActivityLog.user_id)
    )
    return {user_id: count for user_id, count in result.all()}


async def _load_avg_steps(session, user_ids: list[str], period_start: date, period_end: date) -> dict[str, float]:
    if not user_ids:
        return {}
    result = await session.execute(
        select(ActivityLog.user_id, func.avg(ActivityLog.value))
        .where(
            ActivityLog.user_id.in_(user_ids), ActivityLog.metric_type == STEPS_METRIC_TYPE,
            ActivityLog.date >= period_start, ActivityLog.date <= period_end,
        )
        .group_by(ActivityLog.user_id)
    )
    return {user_id: float(avg) for user_id, avg in result.all()}


async def _load_existing_rows(session, department_ids: list[str], period_start: date, period_end: date) -> dict[str, DepartmentRankSnapshot]:
    result = await session.execute(
        select(DepartmentRankSnapshot).where(
            DepartmentRankSnapshot.department_id.in_(department_ids),
            DepartmentRankSnapshot.period_start == period_start,
            DepartmentRankSnapshot.period_end == period_end,
        )
    )
    rows = result.scalars().all()
    return {row.department_id: row for row in rows}


async def _write_snapshots(
    *, session, organization_id: str, department_ids: list[str], user_to_department: dict[str, str],
    total_activities: dict[str, int], avg_steps: dict[str, float],
    period_start: date, period_end: date, existing_rows: dict[str, DepartmentRankSnapshot],
) -> int:
    dept_total_activities: dict[str, int] = {dept_id: 0 for dept_id in department_ids}
    dept_steps_values: dict[str, list[float]] = {dept_id: [] for dept_id in department_ids}

    for user_id, department_id in user_to_department.items():
        dept_total_activities[department_id] += total_activities.get(user_id, 0)
        if user_id in avg_steps:
            dept_steps_values[department_id].append(avg_steps[user_id])

    dept_avg_steps = {
        dept_id: (sum(values) / len(values) if values else 0.0)
        for dept_id, values in dept_steps_values.items()
    }

    max_activities = max(dept_total_activities.values(), default=0)
    max_steps = max(dept_avg_steps.values(), default=0.0)

    scores: dict[str, float] = {}
    for dept_id in department_ids:
        activity_score = (dept_total_activities[dept_id] / max_activities * 100) if max_activities > 0 else 0.0
        steps_score = (dept_avg_steps[dept_id] / max_steps * 100) if max_steps > 0 else 0.0
        scores[dept_id] = round((activity_score + steps_score) / 2, 2)

    ranked = sorted(department_ids, key=lambda d: scores[d], reverse=True)
    ranks = {dept_id: idx + 1 for idx, dept_id in enumerate(ranked)}

    written = 0
    for dept_id in department_ids:
        existing = existing_rows.get(dept_id)
        if existing is not None:
            existing.rank = ranks[dept_id]
            existing.total_activities = dept_total_activities[dept_id]
            existing.avg_daily_steps = dept_avg_steps[dept_id]
            existing.performance_score = scores[dept_id]
        else:
            session.add(DepartmentRankSnapshot(
                department_id=dept_id, rank=ranks[dept_id], total_activities=dept_total_activities[dept_id],
                avg_daily_steps=dept_avg_steps[dept_id], performance_score=scores[dept_id],
                period_start=period_start, period_end=period_end,
            ))
        written += 1

    return written