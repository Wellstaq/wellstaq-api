"""
app/services/event/delete_event.py

Hard delete. Prefer cancel_event() when the event has participation
history worth keeping — this permanently removes the event AND cascades
to all EventParticipant rows (ondelete="CASCADE" on Event relationship).
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event

logger = logging.getLogger(__name__)


async def delete_event(*, session: AsyncSession, organization_id: str, event_id: str) -> None:
    result = await session.execute(
        select(Event).where(Event.id == event_id, Event.organization_id == organization_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found or does not belong to this organization.")

    await session.delete(event)
    await session.flush()
    logger.info(f"Event deleted: {event_id} (org: {organization_id})")