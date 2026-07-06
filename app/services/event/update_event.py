"""
app/services/event/update_event.py

  edit_event    — patch title/description/schedule/status fields
  cancel_event  — dedicated shortcut for status="cancelled", the
                  recommended way to "remove" an event that already has
                  participants/attendance recorded (preserves history,
                  unlike delete_event which is a hard delete).
"""

from __future__ import annotations

import logging
from datetime import date, time

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event

logger = logging.getLogger(__name__)

EVENT_STATUSES = ("scheduled", "ongoing", "completed", "cancelled")


async def edit_event(
    *,
    session: AsyncSession,
    organization_id: str,
    event_id: str,
    title: str | None,
    description: str | None,
    image_url: str | None,
    start_date: date | None,
    end_date: date | None,
    recurrence_rule: str | None,
    event_time: time | None,
    event_status: str | None,
) -> Event:
    result = await session.execute(
        select(Event).where(Event.id == event_id, Event.organization_id == organization_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found or does not belong to this organization.")

    if title is not None:
        event.title = title.strip()
    if description is not None:
        event.description = description
    if image_url is not None:
        event.image_url = image_url
    if start_date is not None:
        event.start_date = start_date
    if end_date is not None:
        event.end_date = end_date
    if recurrence_rule is not None:
        event.recurrence_rule = recurrence_rule
    if event_time is not None:
        event.time = event_time
    if event_status is not None:
        if event_status not in EVENT_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid status '{event_status}'.")
        event.status = event_status

    await session.flush()
    await session.refresh(event)
    logger.info("Event updated", extra={"event_id": event_id, "org_id": organization_id})
    return event


async def cancel_event(*, session: AsyncSession, organization_id: str, event_id: str) -> Event:
    """Preferred over delete_event when the event has participants/attendance worth keeping."""
    result = await session.execute(
        select(Event).where(Event.id == event_id, Event.organization_id == organization_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found or does not belong to this organization.")

    if event.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "Event is already cancelled.")

    event.status = "cancelled"
    await session.flush()
    await session.refresh(event)
    logger.info("Event cancelled", extra={"event_id": event_id, "org_id": organization_id})
    return event