"""
app/jobs/challenges/lifecycle.py

activate_and_close_challenges — the ONLY place Challenge.status
transitions automatically. Every progress-submission path
(submit_progress, submit_activity's fan-out) treats status='active' as
a hard gate for accepting submissions — a challenge stuck in 'draft'
silently accepts nothing until this job runs.
"""

import logging
from datetime import date
from typing import Any

from sqlalchemy import select

from app.core.database import get_db
from app.models import Challenge

logger = logging.getLogger(__name__)


async def activate_and_close_challenges(ctx: dict[str, Any]) -> None:
    """
    Reliably transitions Challenge.status on a schedule:

      draft -> active     when start_date <= today
      active -> completed when end_date < today

    Run frequently enough (see worker.py cron) that a challenge starting
    on a given day is active well before most users submit that day's
    activity.

    TODO(daily-minimum-finalization): when a challenge transitions to
    'completed' here, this is also where the authoritative daily_minimum
    evaluation should run — walking every day in [start_date, end_date]
    and checking each participant had a qualifying entry every day. Not
    yet implemented. Cumulative challenges don't need this — they
    complete inline the moment their target is crossed (see
    check_challenge_completion in app/services/challenge/_helpers.py).
    """
    today = date.today()
    activated = 0
    completed = 0

    async for session in get_db():
        draft_result = await session.execute(
            select(Challenge).where(
                Challenge.status == "draft",
                Challenge.start_date.is_not(None),
                Challenge.start_date <= today,
            )
        )
        for challenge in draft_result.scalars().all():
            challenge.status = "active"
            activated += 1

        active_result = await session.execute(
            select(Challenge).where(
                Challenge.status == "active",
                Challenge.end_date.is_not(None),
                Challenge.end_date < today,
            )
        )
        for challenge in active_result.scalars().all():
            challenge.status = "completed"
            completed += 1
            # TODO(daily-minimum-finalization): see docstring above.

        await session.commit()
        break

    logger.info("Challenge status transitions applied", extra={"activated": activated, "completed": completed})