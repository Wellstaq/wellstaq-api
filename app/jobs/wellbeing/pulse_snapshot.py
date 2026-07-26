"""
app/jobs/wellbeing/pulse_snapshot.py

compute_pulse_snapshots — writes a WellbeingPulseSnapshot for one
closed window, reusing the exact same aggregation logic the live
endpoint uses (compute_pulse_aggregation), so live and snapshot numbers
can never drift apart from having two separate formulas.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.database import get_db
from app.models.wellbeing_survey import PULSE_QUESTION_KEYS, WellbeingPulseSnapshot, WellbeingSurveyWindow
from app.services.wellbeing_survey.live_pulse_service import compute_pulse_aggregation
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def compute_pulse_snapshots(ctx: dict[str, Any], window_id: str) -> None:
    async for session in get_db():
        window_result = await session.execute(select(WellbeingSurveyWindow).where(WellbeingSurveyWindow.id == window_id))
        window = window_result.scalar_one_or_none()
        if window is None:
            logger.warning("compute_pulse_snapshots called for unknown window", extra={"window_id": window_id})
            break

        aggregation = await compute_pulse_aggregation(session=session, window_id=window_id)

        snapshot = WellbeingPulseSnapshot(
            window_id=window_id, organization_id=window.organization_id,
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
        break

    logger.info("Pulse snapshot computed", extra={"window_id": window_id})