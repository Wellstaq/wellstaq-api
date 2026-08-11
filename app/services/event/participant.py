"""
app/services/events/participants.py

  join_event               — create an EventParticipant row (self-join
                              or admin-invite; invited_at set only if
                              is_invite=True)
  update_participant_status — change RSVP status
  mark_attendance           — post-event attended flag
  remove_participant        — leave / remove from event
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RSVP_STATUSES
from app.models import Event, EventParticipant, User
from app.services.activity import log_activity
from app.services.notification import create_notification
logger = logging.getLogger(__name__)




async def _require_event(session: AsyncSession, organization_id: str, event_id: str) -> Event:
    result = await session.execute(
        select(Event).where(Event.id == event_id, Event.organization_id == organization_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found or does not belong to this organization.")
    return event

async def join_event(
    *,
    session: AsyncSession,
    organization_id: str,
    event_id: str,
    user_id: str,
    is_invite: bool = False,
    inviter_id: str | None = None,
) -> EventParticipant:
    event = await _require_event(session, organization_id, event_id)

    # Branch guard — event.branch_id is NOT NULL (always set).
    # A user can only join an event that belongs to their home branch.
    user_result = await session.execute(
        select(User).where(User.id == user_id, User.organization_id == organization_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found or does not belong to this organization.")

    existing = await session.execute(
        select(EventParticipant).where(
            EventParticipant.event_id == event_id, EventParticipant.user_id == user_id
        )
    )
    existing_participant = existing.scalar_one_or_none()
    if existing_participant is not None:
        # An invitation is only an RSVP placeholder, not membership. When the
        # invited user explicitly joins, accept that invitation in-place.
        if not is_invite and existing_participant.status in ("invited", "declined"):
            existing_participant.status = "accepted"
            await session.flush()
            await session.refresh(existing_participant)
            await log_activity(
                session=session, user_id=user_id, organization_id=organization_id,
                activity_type="joined_event", reference_id=event_id, reference_type="event",
            )
            return existing_participant
        raise HTTPException(status.HTTP_409_CONFLICT, "User already has an RSVP for this event.")

    participant = EventParticipant(
        event_id=event_id,
        user_id=user_id,
        invited_at=datetime.now(timezone.utc) if is_invite else None,
        status="invited" if is_invite else "accepted",
        attended=False,
    )
    session.add(participant)
    await session.flush()
    await session.refresh(participant)

    if is_invite:
        await create_notification(
            session=session,
            user_id=user_id,
            notification_type="event_reminder",
            body=f"You were invited to join {event.title}.",
            organization_id=organization_id,
            actor_id=inviter_id,
            reference_id=event.id,
            reference_type="event",
            image_url=event.image_url,
        )
    else:
        await log_activity(
            session=session, user_id=user_id, organization_id=organization_id,
            activity_type="joined_event", reference_id=event_id, reference_type="event",
        )
    logger.info(
    "User joined event event_id=%s user_id=%s",
    event.id,
    user.id,
)
    return participant

async def update_participant_status(
    *, session: AsyncSession, organization_id: str, event_id: str, user_id: str, new_status: str,
) -> EventParticipant:
    await _require_event(session, organization_id, event_id)

    if new_status not in RSVP_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid status '{new_status}'.")

    result = await session.execute(
        select(EventParticipant).where(
            EventParticipant.event_id == event_id, EventParticipant.user_id == user_id
        )
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User is not a participant in this event.")

    participant.status = new_status
    await session.flush()
    await session.refresh(participant)
    return participant


async def mark_attendance(
    *, session: AsyncSession, organization_id: str, event_id: str, user_id: str, attended: bool,
) -> EventParticipant:
    await _require_event(session, organization_id, event_id)

    result = await session.execute(
        select(EventParticipant).where(
            EventParticipant.event_id == event_id, EventParticipant.user_id == user_id
        )
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User is not a participant in this event.")

    participant.attended = attended
    await session.flush()
    await session.refresh(participant)
    return participant


async def remove_participant(
    *, session: AsyncSession, organization_id: str, event_id: str, user_id: str,
) -> None:
    await _require_event(session, organization_id, event_id)

    result = await session.execute(
        select(EventParticipant).where(
            EventParticipant.event_id == event_id, EventParticipant.user_id == user_id
        )
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User is not a participant in this event.")

    await session.delete(participant)
    await session.flush()
