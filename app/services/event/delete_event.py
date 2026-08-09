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
from app.services.storage import release_media_asset
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def delete_event(*, session: AsyncSession, organization_id: str, event_id: str) -> None:
    result = await session.execute(
        select(Event).where(Event.id == event_id, Event.organization_id == organization_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found or does not belong to this organization.")

    image_url = event.image_url  # capture before delete

    await session.delete(event)
    await session.flush()

    if image_url:
        try:
            await release_media_asset(session=session, object_key=extract_object_key(image_url))
        except Exception:
            logger.error(
                "Failed to release event image media asset", exc_info=True,
                extra={"event_id": event_id},
            )

    logger.info(f"Event deleted: {event_id} (org: {organization_id})")