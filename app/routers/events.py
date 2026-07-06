"""
app/routers/events.py

Event management routes.

Permission model:
  event.create / event.update / event.delete are scope="both" — the
  target_branch_id passed to require_scoped_permission is the event's
  actual branch_id (or body.branch_id at creation time).

  Participant actions (join/RSVP/attendance/remove) are treated as
  self-service by default (a user manages their own participation) —
  gated only by get_current_user + org match, not a permission. An
  admin managing OTHER users' participation on their behalf still goes
  through the same endpoints; if you want that restricted to holders of
  event.update, say so and I'll add the scoped check there too.

Routes:
  POST   /organizations/{org_id}/events                                  — create
  GET    /organizations/{org_id}/events                                  — list (optional ?branch_id=)
  GET    /organizations/{org_id}/events/{event_id}                       — get one
  PATCH  /organizations/{org_id}/events/{event_id}                       — edit
  POST   /organizations/{org_id}/events/{event_id}/cancel                — cancel (preserves history)
  DELETE /organizations/{org_id}/events/{event_id}                       — hard delete
  GET    /organizations/{org_id}/events/{event_id}/participants          — list participants
  POST   /organizations/{org_id}/events/{event_id}/participants          — join / invite
  PATCH  /organizations/{org_id}/events/{event_id}/participants/{user_id}/status     — RSVP
  PATCH  /organizations/{org_id}/events/{event_id}/participants/{user_id}/attendance — attendance
  DELETE /organizations/{org_id}/events/{event_id}/participants/{user_id}            — remove
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_permission,
    require_scoped_permission,
    require_same_organization,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.event import (
    EventCreateRequest,
    EventListResponse,
    EventParticipantsListResponse,
    EventResponse,
    EventUpdateRequest,
    ParticipantActionResponse,
    ParticipantAttendanceUpdateRequest,
    ParticipantJoinRequest,
    ParticipantStatusUpdateRequest,
)
from app.services.events import (
    cancel_event,
    create_event,
    delete_event,
    edit_event,
    get_event,
    get_event_participants,
    get_events,
    join_event,
    mark_attendance,
    remove_participant,
    update_participant_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/organizations/{org_id}/events", tags=["events"])


# ── Create ───────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an event",
    description="branch_id is required. Requires `event.create` for that branch (or org-wide).",
)
async def create_event_route(
    org_id: str,
    body: EventCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("event.create")),
) -> EventResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events"):
        await require_scoped_permission(
            db=db, current_user=current_user, permission="event.create", target_branch_id=body.branch_id,
        )

        event = await create_event(
            session=db,
            organization_id=org_id,
            branch_id=body.branch_id,
            title=body.title,
            description=body.description,
            image_url=body.image_url,
            start_date=body.start_date,
            end_date=body.end_date,
            recurrence_rule=body.recurrence_rule,
            event_time=body.time,
            created_by=current_user.user_id,
        )
        await db.commit()
        return EventResponse.model_validate(event)


# ── List ─────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=EventListResponse,
    status_code=status.HTTP_200_OK,
    summary="List events in the organization",
    description="Optionally filter by ?branch_id=. Accessible to any authenticated org member.",
)
async def list_events_route(
    org_id: str,
    branch_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> EventListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events"):
        events, total = await get_events(
            session=db, organization_id=org_id, branch_id=branch_id, offset=offset, limit=limit,
        )
        return EventListResponse(
            items=[EventResponse.model_validate(e) for e in events], total=total, offset=offset, limit=limit,
        )


# ── Get one ──────────────────────────────────────────────────────────────────

@router.get("/{event_id}", response_model=EventResponse, status_code=status.HTTP_200_OK)
async def get_event_route(
    org_id: str,
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> EventResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/events/{event_id}"):
        event = await get_event(session=db, organization_id=org_id, event_id=event_id)
        return EventResponse.model_validate(event)


# ── Edit ─────────────────────────────────────────────────────────────────────

@router.patch(
    "/{event_id}",
    response_model=EventResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an event",
    description="Requires `event.update` for the event's branch (or org-wide).",
)
async def edit_event_route(
    org_id: str,
    event_id: str,
    body: EventUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("event.update")),
) -> EventResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}"):
        event = await get_event(session=db, organization_id=org_id, event_id=event_id)
        await require_scoped_permission(
            db=db, current_user=current_user, permission="event.update", target_branch_id=event.branch_id,
        )

        updated = await edit_event(
            session=db, organization_id=org_id, event_id=event_id,
            title=body.title, description=body.description, image_url=body.image_url,
            start_date=body.start_date, end_date=body.end_date, recurrence_rule=body.recurrence_rule,
            event_time=body.time, event_status=body.status,
        )
        await db.commit()
        return EventResponse.model_validate(updated)


# ── Cancel ───────────────────────────────────────────────────────────────────

@router.post(
    "/{event_id}/cancel",
    response_model=EventResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel an event",
    description=(
        "Sets status='cancelled'. Preferred over DELETE when the event has "
        "participants/attendance worth keeping. Requires `event.update`."
    ),
)
async def cancel_event_route(
    org_id: str,
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("event.update")),
) -> EventResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}/cancel"):
        event = await get_event(session=db, organization_id=org_id, event_id=event_id)
        await require_scoped_permission(
            db=db, current_user=current_user, permission="event.update", target_branch_id=event.branch_id,
        )
        cancelled = await cancel_event(session=db, organization_id=org_id, event_id=event_id)
        await db.commit()
        return EventResponse.model_validate(cancelled)


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete an event",
    description="Hard delete — also removes all participant records. Requires `event.delete`.",
)
async def delete_event_route(
    org_id: str,
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("event.delete")),
):
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}"):
        event = await get_event(session=db, organization_id=org_id, event_id=event_id)
        await require_scoped_permission(
            db=db, current_user=current_user, permission="event.delete", target_branch_id=event.branch_id,
        )
        await delete_event(session=db, organization_id=org_id, event_id=event_id)
        await db.commit()


# ── Participants ─────────────────────────────────────────────────────────────

@router.get(
    "/{event_id}/participants",
    response_model=EventParticipantsListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_event_participants_route(
    org_id: str,
    event_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> EventParticipantsListResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}/participants"):
        rows, total = await get_event_participants(
            session=db, organization_id=org_id, event_id=event_id, offset=offset, limit=limit,
        )
        items = [
            EventParticipantInfo(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                status=participant.status,
                attended=participant.attended,
                invited_at=participant.invited_at.isoformat() if participant.invited_at else None,
            )
            for participant, user in rows
        ]
        return EventParticipantsListResponse(items=items, total=total, offset=offset, limit=limit)


@router.post(
    "/{event_id}/participants",
    response_model=ParticipantActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Join an event, or invite a user to it",
)
async def join_event_route(
    org_id: str,
    event_id: str,
    body: ParticipantJoinRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ParticipantActionResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}/participants"):
        await join_event(
            session=db, organization_id=org_id, event_id=event_id,
            user_id=body.user_id, is_invite=body.is_invite,
        )
        await db.commit()
        return ParticipantActionResponse(event_id=event_id, user_id=body.user_id, message="Joined event.")


@router.patch(
    "/{event_id}/participants/{user_id}/status",
    response_model=ParticipantActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a participant's RSVP status",
)
async def update_participant_status_route(
    org_id: str,
    event_id: str,
    user_id: str,
    body: ParticipantStatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ParticipantActionResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}/participants/{user_id}/status"):
        await update_participant_status(
            session=db, organization_id=org_id, event_id=event_id, user_id=user_id, new_status=body.status,
        )
        await db.commit()
        return ParticipantActionResponse(event_id=event_id, user_id=user_id, message="RSVP status updated.")


@router.patch(
    "/{event_id}/participants/{user_id}/attendance",
    response_model=ParticipantActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark a participant's attendance",
    description="Requires `event.update` — recording attendance is an organizer action, not self-service.",
)
async def mark_attendance_route(
    org_id: str,
    event_id: str,
    user_id: str,
    body: ParticipantAttendanceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("event.update")),
) -> ParticipantActionResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}/participants/{user_id}/attendance"):
        event = await get_event(session=db, organization_id=org_id, event_id=event_id)
        await require_scoped_permission(
            db=db, current_user=current_user, permission="event.update", target_branch_id=event.branch_id,
        )
        await mark_attendance(
            session=db, organization_id=org_id, event_id=event_id, user_id=user_id, attended=body.attended,
        )
        await db.commit()
        return ParticipantActionResponse(event_id=event_id, user_id=user_id, message="Attendance recorded.")


@router.delete(
    "/{event_id}/participants/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a participant / leave the event",
)
async def remove_participant_route(
    org_id: str,
    event_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}/participants/{user_id}"):
        await remove_participant(session=db, organization_id=org_id, event_id=event_id, user_id=user_id)
        await db.commit()