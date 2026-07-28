"""
app/services/target/progress_service.py

Compares a Target's target_value against the branch's real ActivityLog
sum for the same metric_type/period — the actual "how are we doing
against our goal" read.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog, Branch
from app.models.snapshot_target import Target
from app.services.target._helpers import period_date_range


async def get_target_progress(
    *, session: AsyncSession, organization_id: str, branch_id: str, metric_type: str, period_type: str, period: str
) -> dict:
    normalized_metric_type = metric_type.strip().lower()

    target_result = await session.execute(
        select(Target).where(
            Target.organization_id == organization_id, Target.branch_id == branch_id,
            Target.metric_type == normalized_metric_type, Target.period == period,
        )
    )
    target = target_result.scalar_one_or_none()

    window_start, window_end = period_date_range(period_type=period_type, period=period)

    total_result = await session.execute(
        select(func.sum(ActivityLog.value)).where(
            ActivityLog.organization_id == organization_id, ActivityLog.branch_id == branch_id,
            ActivityLog.metric_type == normalized_metric_type,
            ActivityLog.date >= window_start, ActivityLog.date <= window_end,
        )
    )
    current_value = total_result.scalar_one() or 0

    percent_complete = None
    if target is not None and target.target_value:
        percent_complete = min(100, round((float(current_value) / float(target.target_value)) * 100, 2))

    return {
        "branch_id": branch_id,
        "metric_type": normalized_metric_type,
        "period": period,
        "target_value": target.target_value if target else None,
        "current_value": current_value,
        "percent_complete": percent_complete,
        "has_target": target is not None,
    }


async def list_branch_targets(*, session: AsyncSession, organization_id: str, branch_id: str) -> list[Target]:
    result = await session.execute(
        select(Target).where(Target.organization_id == organization_id, Target.branch_id == branch_id)
        .order_by(Target.period.desc())
    )
    return list(result.scalars().all())