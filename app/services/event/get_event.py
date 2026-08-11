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


async def get_event(
    *,
    session: AsyncSession,
    organization_id: str,
    event_id: str,
) -> tuple[Event, int]:
    """Returns (event, participant_count)."""

    participant_count_sq = (
        select(
            EventParticipant.event_id,
            func.count().label("cnt"),
        )
        .where(EventParticipant.status.in_(("accepted", "pending")))
        .group_by(EventParticipant.event_id)
        .subquery()
    )

    result = await session.execute(
        select(Event, func.coalesce(participant_count_sq.c.cnt, 0))
        .outerjoin(participant_count_sq, participant_count_sq.c.event_id == Event.id)
        .where(
            Event.id == event_id,
            Event.organization_id == organization_id,
        )
    )

    row = result.one_or_none()

    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Event not found or does not belong to this organization.",
        )

    event, participant_count = row
    return event, participant_count


async def get_events(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[tuple[Event, int]], int]:
    """List events in an org, optionally filtered to one branch.
    Returns (event, participant_count) pairs.
    """
    filters = [Event.organization_id == organization_id]
    if branch_id is not None:
        filters.append(Event.branch_id == branch_id)

    count_result = await session.execute(
        select(func.count()).select_from(Event).where(*filters)
    )
    total = count_result.scalar_one()

    participant_count_sq = (
        select(EventParticipant.event_id, func.count().label("cnt"))
        .where(EventParticipant.status.in_(("accepted", "pending")))
        .group_by(EventParticipant.event_id)
        .subquery()
    )

    result = await session.execute(
        select(Event, func.coalesce(participant_count_sq.c.cnt, 0))
        .outerjoin(participant_count_sq, participant_count_sq.c.event_id == Event.id)
        .where(*filters)
        .order_by(Event.start_date.desc())
        .offset(offset)
        .limit(limit)
    )

    rows = result.all()
    return rows, total


async def get_event_participants(
    *,
    session: AsyncSession,
    organization_id: str,
    event_id: str,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[tuple[EventParticipant, User]], int]:
    """Returns (participant, user) pairs so the router can build a combined view."""

    # existence check
    await get_event(
        session=session,
        organization_id=organization_id,
        event_id=event_id,
    )

    count_result = await session.execute(
        select(func.count())
        .select_from(EventParticipant)
        .where(EventParticipant.event_id == event_id)
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
