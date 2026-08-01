"""
app/jobs/wellbeing/pulse_snapshot.py
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.jobs.shared import get_job_session
from app.models import Branch
from app.models.wellbeing_survey import WellbeingPulseSnapshot, WellbeingSurveyWindow
from app.services.wellbeing_survey.live_pulse_service import compute_pulse_aggregation

logger = logging.getLogger(__name__)


async def compute_pulse_snapshots(ctx: dict[str, Any], window_id: str) -> None:
    scopes: list[str | None] = [None]

    async with get_job_session() as session:
        window_result = await session.execute(select(WellbeingSurveyWindow).where(WellbeingSurveyWindow.id == window_id))
        window = window_result.scalar_one_or_none()
        if window is None:
            logger.warning("compute_pulse_snapshots called for unknown window", extra={"window_id": window_id})
            return

        branches_result = await session.execute(select(Branch.id).where(Branch.organization_id == window.organization_id))
        scopes.extend(row[0] for row in branches_result.all())

        for branch_id in scopes:
            aggregation = await compute_pulse_aggregation(session=session, window_id=window_id, branch_id=branch_id)

            snapshot = WellbeingPulseSnapshot(
                window_id=window_id, organization_id=window.organization_id, branch_id=branch_id,
                stress_manageability_pct=aggregation["per_question"]["stress_manageability"]["percent"],
                energy_recovery_pct=aggregation["per_question"]["energy_recovery"]["percent"],
                connection_belonging_pct=aggregation["per_question"]["connection_belonging"]["percent"],
                workload_sustainability_pct=aggregation["per_question"]["workload_sustainability"]["percent"],
                workplace_comfort_pct=aggregation["per_question"]["workplace_comfort"]["percent"],
                priority_support_pct=aggregation["priority_support_pct"],
                needs_attention_pct=aggregation["needs_attention_pct"],
                doing_well_pct=aggregation["doing_well_pct"],
                respondent_count=aggregation["respondent_count"],
            )
            session.add(snapshot)

        await session.commit()

    logger.info("Pulse snapshots computed", extra={"window_id": window_id, "scopes_computed": len(scopes)})