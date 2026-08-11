"""
app/services/target/target_service.py

set_target upserts (per the model docstring's own guidance — one target
per branch/metric_type/period, service layer must upsert not blindly
insert to avoid the UniqueConstraint's duplicate-key error).
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshot_target import Target
from app.services.target._helpers import validate_branch

logger = logging.getLogger(__name__)


async def set_target(
    *, session: AsyncSession, organization_id: str, branch_id: str, metric_type: str, period: str, target_value
) -> Target:
    await validate_branch(session=session, organization_id=organization_id, branch_id=branch_id)

    normalized_metric_type = metric_type.strip().lower()

    if target_value <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "target_value must be positive.")

    existing = await session.execute(
        select(Target).where(
            Target.branch_id == branch_id, Target.metric_type == normalized_metric_type, Target.period == period,
        )
    )
    target = existing.scalar_one_or_none()

    if target is not None:
        target.target_value = target_value
    else:
        target = Target(
            organization_id=organization_id, branch_id=branch_id,
            metric_type=normalized_metric_type, period=period, target_value=target_value,
        )
        session.add(target)

    await session.flush()
    await session.refresh(target)

    logger.info("Target set", extra={"branch_id": branch_id, "metric_type": normalized_metric_type, "period": period})
    return target


async def delete_target(*, session: AsyncSession, organization_id: str, target_id: str) -> None:
    from app.services.target._helpers import get_target_or_404

    target = await get_target_or_404(session=session, organization_id=organization_id, target_id=target_id)
    await session.delete(target)
    await session.flush()


async def get_monthly_step_targets(
    *, session: AsyncSession, organization_id: str, branch_id: str | None,
    period_start: date, period_end: date,
) -> dict[str, float]:
    """Return monthly step targets, summing branch targets for org scope."""
    query = select(Target.period, func.sum(Target.target_value)).where(
        Target.organization_id == organization_id,
        Target.metric_type == "steps",
        Target.period >= period_start.strftime("%Y-%m"),
        Target.period <= period_end.strftime("%Y-%m"),
    )
    if branch_id:
        query = query.where(Target.branch_id == branch_id)
    query = query.group_by(Target.period)
    return {period: float(value) for period, value in (await session.execute(query)).all()}
