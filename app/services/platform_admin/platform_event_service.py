"""
app/services/platform_admin/platform_event_service.py

Admin-side CRUD for featured events — see app/models/platform_event.py
for why these are a separate model from org Events. The employee/
individual-facing read + join side lives in
app/services/platform_event_service.py (no platform_admin prefix,
since it's called from the ordinary authenticated-user routers, not
/app-admin/*) — split the same way challenge creation vs challenge
participation are split across different permission boundaries.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AdminUser, Organization, PlatformEvent, PlatformEventOrganization, PlatformEventParticipant


async def get_platform_event_or_404(*, session: AsyncSession, event_id: str) -> PlatformEvent:
    result = await session.execute(
        select(PlatformEvent).options(selectinload(PlatformEvent.organizations).selectinload(PlatformEventOrganization.organization), selectinload(PlatformEvent.creator))
        .where(PlatformEvent.id == event_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Platform event not found.")
    return event


async def _serialize(session: AsyncSession, event: PlatformEvent) -> dict:
    participant_count = (await session.execute(
        select(func.count()).select_from(PlatformEventParticipant).where(PlatformEventParticipant.event_id == event.id)
    )).scalar_one()
    return {
        "id": event.id, "title": event.title, "description": event.description, "image_url": event.image_url,
        "start_date": event.start_date, "end_date": event.end_date, "time": event.time, "timezone": event.timezone,
        "recurrence_rule": event.recurrence_rule, "status": event.status, "audience": event.audience,
        "organizations": [{"id": o.organization.id, "name": o.organization.business_name} for o in event.organizations],
        "participant_count": participant_count,
        "created_by": {"id": event.creator.id, "name": f"{event.creator.first_name} {event.creator.last_name}".strip()} if event.creator else None,
        "created_at": event.created_at, "updated_at": event.updated_at,
    }


async def get_platform_event(*, session: AsyncSession, event_id: str) -> dict:
    event = await get_platform_event_or_404(session=session, event_id=event_id)
    return await _serialize(session, event)


async def list_platform_events(
    *, session: AsyncSession, q: str | None, status_filter: str | None, audience: str | None, sort: str | None, offset: int, limit: int,
) -> tuple[list[dict], int]:
    query = select(PlatformEvent).options(selectinload(PlatformEvent.organizations).selectinload(PlatformEventOrganization.organization), selectinload(PlatformEvent.creator))
    count_query = select(func.count()).select_from(PlatformEvent)
    if q:
        query, count_query = query.where(PlatformEvent.title.ilike(f"%{q}%")), count_query.where(PlatformEvent.title.ilike(f"%{q}%"))
    if status_filter:
        query, count_query = query.where(PlatformEvent.status == status_filter), count_query.where(PlatformEvent.status == status_filter)
    if audience:
        query, count_query = query.where(PlatformEvent.audience == audience), count_query.where(PlatformEvent.audience == audience)
    order = PlatformEvent.start_date.desc() if sort == "-start_date" else PlatformEvent.start_date.asc()

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.order_by(order).offset(offset).limit(limit))).scalars().all()
    return [await _serialize(session, e) for e in rows], total


async def create_platform_event(*, session: AsyncSession, created_by: str, body: dict) -> dict:
    if body["audience"] == "selected_organizations" and not body.get("organization_ids"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "At least one organization is required for audience='selected_organizations'.")
    if body.get("end_date") and body["end_date"] < body["start_date"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "end_date cannot be before start_date.")

    event = PlatformEvent(
        title=body["title"].strip(), description=body.get("description"), image_url=body.get("image_url"),
        start_date=body["start_date"], end_date=body.get("end_date"), time=body["time"], timezone=body["timezone"],
        audience=body["audience"], created_by=created_by,
    )
    session.add(event)
    await session.flush()

    if body["audience"] == "selected_organizations":
        for org_id in body["organization_ids"]:
            org_exists = (await session.execute(select(Organization.id).where(Organization.id == org_id))).scalar_one_or_none()
            if org_exists is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Organization '{org_id}' not found.")
            session.add(PlatformEventOrganization(event_id=event.id, organization_id=org_id))
        await session.flush()

    return await _serialize(session, await get_platform_event_or_404(session=session, event_id=event.id))


async def update_platform_event(*, session: AsyncSession, event_id: str, body: dict) -> dict:
    event = await get_platform_event_or_404(session=session, event_id=event_id)

    for field in ("title", "description", "image_url", "start_date", "end_date", "time", "timezone"):
        if field in body and body[field] is not None:
            setattr(event, field, body[field].strip() if field == "title" else body[field])
    if body.get("audience"):
        event.audience = body["audience"]
        await session.execute(PlatformEventOrganization.__table__.delete().where(PlatformEventOrganization.event_id == event.id))
        if body["audience"] == "selected_organizations":
            for org_id in body.get("organization_ids", []):
                session.add(PlatformEventOrganization(event_id=event.id, organization_id=org_id))

    if event.end_date and event.end_date < event.start_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "end_date cannot be before start_date.")

    await session.flush()
    return await _serialize(session, await get_platform_event_or_404(session=session, event_id=event_id))


async def cancel_platform_event(*, session: AsyncSession, event_id: str) -> dict:
    event = await get_platform_event_or_404(session=session, event_id=event_id)
    event.status = "cancelled"
    await session.flush()
    # TODO: notify joined participants — mirrors org Event.cancel's
    # intent, deferred until a platform-event notification type exists.
    return await _serialize(session, event)


async def delete_platform_event(*, session: AsyncSession, event_id: str) -> None:
    event = await get_platform_event_or_404(session=session, event_id=event_id)
    await session.delete(event)
    await session.flush()
