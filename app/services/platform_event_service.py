"""
app/services/platform_event_service.py

Employee/individual-facing read + join for featured events — see
app/services/platform_admin/platform_event_service.py for the
admin-side CRUD this reads from. Visibility follows PlatformEvent.audience:
'everyone' always shows; 'employees' shows to any org member;
'selected_organizations' only to members of those specific orgs;
'individuals' never shows here (no org-less user type exists yet —
see the Individuals open question in docs/ADMIN_API_CONTRACT.md).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import PlatformEvent, PlatformEventOrganization, PlatformEventParticipant


def _visible_to_org_clause(organization_id: str):
    return or_(
        PlatformEvent.audience.in_(("everyone", "employees")),
        PlatformEvent.id.in_(
            select(PlatformEventOrganization.event_id).where(PlatformEventOrganization.organization_id == organization_id)
        ),
    )


async def list_platform_events_for_user(*, session: AsyncSession, organization_id: str, offset: int, limit: int) -> tuple[list[PlatformEvent], int]:
    base = select(PlatformEvent).where(
        PlatformEvent.status != "cancelled", _visible_to_org_clause(organization_id),
    )
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await session.execute(
        base.order_by(PlatformEvent.start_date.asc()).offset(offset).limit(limit)
    )).scalars().all()
    return list(rows), total


async def join_platform_event(*, session: AsyncSession, event_id: str, user_id: str, organization_id: str) -> None:
    event = (await session.execute(select(PlatformEvent).where(PlatformEvent.id == event_id))).scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found.")
    if event.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "This event has been cancelled.")

    visible = (await session.execute(
        select(PlatformEvent.id).where(PlatformEvent.id == event_id, _visible_to_org_clause(organization_id))
    )).scalar_one_or_none()
    if visible is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This event is not available to your organization.")

    existing = (await session.execute(
        select(PlatformEventParticipant).where(PlatformEventParticipant.event_id == event_id, PlatformEventParticipant.user_id == user_id)
    )).scalar_one_or_none()
    if existing is not None:
        return  # idempotent — already joined

    session.add(PlatformEventParticipant(event_id=event_id, user_id=user_id))
    await session.flush()


async def leave_platform_event(*, session: AsyncSession, event_id: str, user_id: str) -> None:
    result = await session.execute(
        select(PlatformEventParticipant).where(PlatformEventParticipant.event_id == event_id, PlatformEventParticipant.user_id == user_id)
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You have not joined this event.")
    await session.delete(participant)
    await session.flush()
