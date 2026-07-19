"""
app/services/kpi/get_kpi.py

Read-only. KPISnapshot rows are written by compute_kpi_snapshots
(app/jobs/analytics/kpi.py) — that job's real logic is still blocked on
an unresolved design question (branch-vs-org-wide figures), so this
read path currently has nothing to serve until that job is implemented
and actually populates rows. The route/service themselves are complete
and correct against the model as designed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KPISnapshot


async def get_branch_kpis(*, session: AsyncSession, organization_id: str, branch_id: str, limit: int = 50):
    result = await session.execute(
        select(KPISnapshot)
        .where(KPISnapshot.organization_id == organization_id, KPISnapshot.branch_id == branch_id)
        .order_by(KPISnapshot.period_start.desc())
        .limit(limit)
    )
    return list(result.scalars().all())