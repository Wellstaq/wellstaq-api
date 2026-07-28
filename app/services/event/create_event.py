"""
app/services/event/create_event.py
"""

from __future__ import annotations

import logging
from datetime import date, time

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, Event, Organization
from app.services.payment import enforce_plan_limit

logger = logging.getLogger(__name__)


async def create_event(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    title: str,
    description: str | None,
    image_url: str | None,
    start_date: date,
    end_date: date | None,
    recurrence_rule: str | None,
    event_time: time,
    created_by: str,
) -> Event:
    """
    Create an event. Every event must belong to a branch — branch_id is
    required, and organization_id is set from the branch's own org (not
    trusted from the caller) to guarantee the consistency invariant.

    Raises:
      404 — organization not found.
      404 — branch not found or does not belong to this organization.
    """
    org_result = await session.execute(
        select(Organization).where(Organization.id == organization_id)
    )
    if org_result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found.")

    branch_result = await session.execute(
        select(Branch).where(Branch.id == branch_id, Branch.organization_id == organization_id)
    )
    if branch_result.scalar_one_or_none() is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Branch not found or does not belong to this organization.",
        )
    existing_count = (await session.execute(
        select(func.count()).select_from(Event).where(Event.organization_id == organization_id)
    )).scalar_one()

    await enforce_plan_limit(
        session=session, organization_id=organization_id, limit_key="max_events", current_count=existing_count,
    )

    event = Event(
        title=title.strip(),
        description=description,
        image_url=image_url,
        start_date=start_date,
        end_date=end_date,
        recurrence_rule=recurrence_rule,
        time=event_time,
        status="scheduled",
        organization_id=organization_id,  # trusted source: the org param, cross-checked against branch above
        branch_id=branch_id,
        created_by=created_by,
    )
    session.add(event)
    await session.flush()
    await session.refresh(event)

    logger.info("Event created", extra={"event_id": event.id, "branch_id": branch_id, "org_id": organization_id})
    return event