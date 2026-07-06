"""
app/services/event/get_event.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, EventParticipant, User

logger = logging.getLogger(__name__)


async def get_event(*, session: AsyncSession, organization_id: str, event_id: str) -> Event:
    result = await session.execute(
        select(Event).where(Event.id == event_id, Event.organization_id == organization_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found or does not belong to this organization.")
    return event


async def get_events(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Event], int]:
    """List events in an org, optionally filtered to one branch."""
    filters = [Event.organization_id == organization_id]
    if branch_id is not None:
        filters.append(Event.branch_id == branch_id)

    count_result = await session.execute(select(func.count()).select_from(Event).where(*filters))
    total = count_result.scalar_one()

    result = await session.execute(
        select(Event).where(*filters).order_by(Event.start_date.desc()).offset(offset).limit(limit)
    )
    events = result.scalars().all()
    return events, total


async def get_event_participants(
    *,
    session: AsyncSession,
    organization_id: str,
    event_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[tuple[EventParticipant, User]], int]:
    """Returns (participant, user) pairs so the router can build a combined view."""
    await get_event(session=session, organization_id=organization_id, event_id=event_id)  # existence check

    count_result = await session.execute(
        select(func.count()).select_from(EventParticipant).where(EventParticipant.event_id == event_id)
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(EventParticipant, User)
        .join(User, User.id == EventParticipant.user_id)
        .where(EventParticipant.event_id == event_id)
        .order_by(User.first_name, User.last_name)
        .offset(offset)
        .limit(limit)
    )
    rows = result.all()
    return rows, total