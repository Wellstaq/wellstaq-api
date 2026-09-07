"""Automatic event status transitions."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import select

from app.jobs.shared import get_job_session
from app.models import Event


logger = logging.getLogger(__name__)


async def activate_scheduled_events(ctx: dict[str, Any]) -> None:
    """Move due scheduled events to ongoing; completion remains manual."""
    today = date.today()
    activated = 0

    async with get_job_session() as session:
        result = await session.execute(
            select(Event).where(
                Event.status == "scheduled",
                Event.start_date <= today,
            )
        )
        for event in result.scalars().all():
            event.status = "ongoing"
            activated += 1

        await session.commit()

    logger.info("Event status transitions applied", extra={"activated": activated})
