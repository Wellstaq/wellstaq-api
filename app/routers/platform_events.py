"""
app/routers/platform_events.py

Employee/individual-facing read + join for featured events (see
app/services/platform_event_service.py). No org_id prefix — matches
the admin contract doc's own proposal ("GET /platform-events").
Unauthenticated-by-org-suspension same as every other route: gated by
require_active_subscription like the rest of the employee-facing API.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_active_subscription
from app.core.database import get_db
from app.core.guards import request_guard
from app.services import platform_event_service

router = APIRouter(prefix="/platform-events", tags=["platform-events"])


@router.get("")
async def list_platform_events_route(
    offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_active_subscription),
):
    async with request_guard(db, "/platform-events"):
        rows, total = await platform_event_service.list_platform_events_for_user(
            session=db, organization_id=current_user.organization_id, offset=offset, limit=limit,
        )
        items = [{
            "id": e.id, "title": e.title, "description": e.description, "image_url": e.image_url,
            "start_date": e.start_date, "end_date": e.end_date, "time": e.time, "timezone": e.timezone,
            "status": e.status,
        } for e in rows]
        return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.post("/{event_id}/join", status_code=status.HTTP_201_CREATED)
async def join_platform_event_route(
    event_id: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_active_subscription),
):
    async with request_guard(db, "/platform-events/{event_id}/join"):
        await platform_event_service.join_platform_event(
            session=db, event_id=event_id, user_id=current_user.user_id, organization_id=current_user.organization_id,
        )
        await db.commit()
        return {"success": True}


@router.delete("/{event_id}/join", status_code=status.HTTP_200_OK)
async def leave_platform_event_route(
    event_id: str, db: AsyncSession = Depends(get_db), current_user: CurrentUser = Depends(require_active_subscription),
):
    async with request_guard(db, "/platform-events/{event_id}/join"):
        await platform_event_service.leave_platform_event(session=db, event_id=event_id, user_id=current_user.user_id)
        await db.commit()
        return {"success": True}
