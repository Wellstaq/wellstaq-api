"""
app/services/wellbeing_survey/snapshot_service.py

Read access to CLOSED windows' snapshots — written exclusively by
compute_pulse_snapshots (background job), never here.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wellbeing_survey import WellbeingPulseSnapshot



async def get_latest_pulse_snapshot(
    *, session: AsyncSession, organization_id: str, branch_id: str | None = None
) -> WellbeingPulseSnapshot | None:
    query = select(WellbeingPulseSnapshot).where(
        WellbeingPulseSnapshot.organization_id == organization_id, WellbeingPulseSnapshot.branch_id == branch_id,
    )
    result = await session.execute(query.order_by(WellbeingPulseSnapshot.computed_at.desc()).limit(1))
    return result.scalar_one_or_none()


async def get_pulse_history(
    *, session: AsyncSession, organization_id: str, branch_id: str | None = None, limit: int = 12
) -> list[WellbeingPulseSnapshot]:
    query = select(WellbeingPulseSnapshot).where(
        WellbeingPulseSnapshot.organization_id == organization_id, WellbeingPulseSnapshot.branch_id == branch_id,
    )
    result = await session.execute(query.order_by(WellbeingPulseSnapshot.computed_at.desc()).limit(limit))
    return list(result.scalars().all())