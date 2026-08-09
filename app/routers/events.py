"""
app/routers/events.py

Group chat routes for clubs and events. conversation_type is 'club' or
'event' (see GroupChatMessage model). Pinning is restricted to
`chat.moderate` — an admin/manager permission, not structural.

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
  POST   /organizations/{org_id}/events
  GET    /organizations/{org_id}/events
  GET    /organizations/{org_id}/events/{event_id}
  PATCH  /organizations/{org_id}/events/{event_id}
  POST   /organizations/{org_id}/events/{event_id}/cancel
  DELETE /organizations/{org_id}/events/{event_id}
  GET    /organizations/{org_id}/events/{event_id}/participants
  POST   /organizations/{org_id}/events/{event_id}/participants
  PATCH  /organizations/{org_id}/events/{event_id}/participants/{user_id}/status
  PATCH  /organizations/{org_id}/events/{event_id}/participants/{user_id}/attendance
  DELETE /organizations/{org_id}/events/{event_id}/participants/{user_id}
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
    require_active_subscription,
)
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.event import (
    EventCreateRequest,
    EventListResponse,
    EventParticipantsListResponse,
    EventResponse,
    EventParticipantInfo,
    EventUpdateRequest,
    ParticipantActionResponse,
    ParticipantAttendanceUpdateRequest,
    ParticipantJoinRequest,
    ParticipantStatusUpdateRequest,
)
from app.services.event import (
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
    description="""
Create a new event scoped to a specific branch within the organization.

**Who can call this:**
Requires the `event.create` permission, granted either org-wide or scoped
to the target branch. Super admins always pass this check.

**Request body fields:**
| Field             | Type         | Required | Notes |
|-------------------|--------------|----------|-------|
| `title`           | string       | ✅       | Display name of the event. Max 255 chars. |
| `branch_id`       | UUID string  | ✅       | The branch this event belongs to. Every event must have a branch. |
| `start_date`      | date         | ✅       | Format: `YYYY-MM-DD`. First occurrence if recurring. |
| `time`            | time         | ✅       | Format: `HH:MM:SS`. Start time of the event. |
| `description`     | string       | ❌       | Optional longer text description. |
| `image_url`       | string       | ❌       | URL to a cover image. Max 2048 chars. |
| `end_date`        | date         | ❌       | Format: `YYYY-MM-DD`. Last occurrence if recurring; null = one-time or open-ended. |
| `recurrence_rule` | string       | ❌       | Null = one-time event. Pattern examples: `weekly_monday`, `monthly_first`. |

**Branch rule:**
Only users whose home branch matches `branch_id` can later join this event.
Cross-branch joins are blocked at the service layer.
""",
)
async def create_event_route(
    org_id: str,
    body: EventCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("event.create")),
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
    description="""
Returns a paginated list of events belonging to the organization.

**Who can call this:**
Any authenticated member of the organization. No special permission required.

**Query parameters:**
| Parameter   | Type        | Default | Notes |
|-------------|-------------|---------|-------|
| `branch_id` | UUID string | —       | Optional. Filter events to a specific branch. Omit to return events from all branches. |
| `offset`    | integer     | `0`     | Pagination offset. Must be ≥ 0. |
| `limit`     | integer     | `50`    | Number of results per page. Min `1`, max `200`. |

**Tip:** To see only your branch's events, pass `?branch_id=<your_branch_id>`.
""",
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
            items=[
                EventResponse.model_validate({**e.__dict__, "participant_count": count})
                for e, count in events
            ],
            total=total, offset=offset, limit=limit,
        )

# ── Get one ──────────────────────────────────────────────────────────────────

@router.get(
    "/{event_id}",
    response_model=EventResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a single event",
    description="""
Fetch the full details of a single event by its ID.

**Who can call this:**
Any authenticated member of the organization. No special permission required.

**Path parameters:**
| Parameter  | Type        | Notes |
|------------|-------------|-------|
| `org_id`   | UUID string | The organization the event belongs to. |
| `event_id` | UUID string | The event to retrieve. |

Returns `404` if the event doesn't exist or doesn't belong to this organization.
""",
)
async def get_event_route(
    org_id: str,
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> EventResponse:
    require_same_organization(org_id, current_user)
    async with request_guard(db, "/organizations/{org_id}/events/{event_id}"):
        event = await get_event(session=db, organization_id=org_id, event_id=event_id)
        return EventResponse.model_validate(
            {
                **event.__dict__,
                "participant_count": participant_count,
            }
        )


# ── Edit ─────────────────────────────────────────────────────────────────────

@router.patch(
    "/{event_id}",
    response_model=EventResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an event",
    description="""
Partially update an existing event. Only fields included in the request body
are changed — all others remain untouched.

**Who can call this:**
Requires the `event.update` permission scoped to the event's branch (or org-wide).
Super admins always pass this check.

**Updatable fields:**
| Field             | Type   | Notes |
|-------------------|--------|-------|
| `title`           | string | New display name. Max 255 chars. |
| `description`     | string | New description text. |
| `image_url`       | string | New cover image URL. Max 2048 chars. |
| `start_date`      | date   | Format: `YYYY-MM-DD`. |
| `end_date`        | date   | Format: `YYYY-MM-DD`. Set to `null` to remove. |
| `recurrence_rule` | string | Set to `null` to make it a one-time event. |
| `time`            | time   | Format: `HH:MM:SS`. |
| `status`          | string | Allowed values: `scheduled` \| `ongoing` \| `completed` \| `cancelled`. Prefer the dedicated `/cancel` endpoint to cancel — use this only when correcting a status. |

**Note:** `branch_id` and `organization_id` cannot be changed after creation.
""",
)
async def edit_event_route(
    org_id: str,
    event_id: str,
    body: EventUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("event.update")),
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
    description="""
Sets the event's status to `cancelled`. The event record and all participant
history are preserved — this is a soft action, not a delete.

**Who can call this:**
Requires `event.update` scoped to the event's branch (or org-wide).

**When to use this vs DELETE:**
- Use `/cancel` when the event has participants, attendance records, or any
  history worth keeping (e.g. it happened but was cut short, or was called
  off last minute).
- Use `DELETE` only when the event was created by mistake and has no
  meaningful history to preserve.

**No request body required.** The status is always set to `cancelled`.

Returns the updated event object with `status: "cancelled"`.
""",
)
async def cancel_event_route(
    org_id: str,
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("event.update")),
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
    description="""
Hard deletes the event and all associated data (participants, attendance
records). This action is **irreversible**.

**Who can call this:**
Requires the `event.delete` permission scoped to the event's branch (or org-wide).
Super admins always pass this check.

**When to use this vs /cancel:**
- Prefer `/cancel` if the event has any participants or attendance history
  worth preserving.
- Use this only when the event was created in error and has no meaningful data.

Returns `204 No Content` on success. No response body.
""",
)
async def delete_event_route(
    org_id: str,
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("event.delete")),
):
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}"):
        event = await get_event(session=db, organization_id=org_id, event_id=event_id)
        await require_scoped_permission(
            db=db, current_user=current_user, permission="event.delete", target_branch_id=event.branch_id,
        )
        await delete_event(session=db, organization_id=org_id, event_id=event_id)
        await db.commit()


# ── List participants ─────────────────────────────────────────────────────────

@router.get(
    "/{event_id}/participants",
    response_model=EventParticipantsListResponse,
    status_code=status.HTTP_200_OK,
    summary="List participants of an event",
    description="""
Returns a paginated list of users who have joined or been invited to this event,
along with their RSVP status and attendance flag.

**Who can call this:**
Any authenticated member of the organization. No special permission required.

**Query parameters:**
| Parameter | Type    | Default | Notes |
|-----------|---------|---------|-------|
| `offset`  | integer | `0`     | Pagination offset. Must be ≥ 0. |
| `limit`   | integer | `50`    | Results per page. Min `1`, max `200`. |

**Response fields per participant:**
| Field        | Type    | Notes |
|--------------|---------|-------|
| `user_id`    | string  | UUID of the participant. |
| `first_name` | string  | |
| `last_name`  | string  | |
| `email`      | string  | |
| `status`     | string  | RSVP status: `invited` \| `accepted` \| `declined` \| `pending`. |
| `attended`   | boolean | `true` once attendance has been marked post-event. |
| `invited_at` | string  | ISO 8601 timestamp if the user was formally invited; `null` for self-joins. |
""",
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


# ── Join / invite ─────────────────────────────────────────────────────────────

@router.post(
    "/{event_id}/participants",
    response_model=ParticipantActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Join an event or invite a user to it",
    description="""
Adds a user as a participant in this event. Supports two modes controlled
by the `is_invite` flag in the request body.

**Who can call this:**
Any authenticated member of the organization. No special permission required
for self-joins. Admins can use `is_invite: true` to add other users on their behalf.

**Branch rule:**
The user being added must belong to the **same branch** as the event.
Cross-branch joins return `403 Forbidden`.

**Request body:**
| Field       | Type    | Required | Notes |
|-------------|---------|----------|-------|
| `user_id`   | string  | ✅       | UUID of the user to add. Pass the current user's own ID for a self-join. |
| `is_invite` | boolean | ❌       | Default: `false`. Set to `true` when an admin is adding someone else — sets `status` to `"invited"` and records `invited_at`. Self-joins get `status: "pending"`. |

**Status assigned automatically:**
- `is_invite: false` → `status: "pending"`
- `is_invite: true`  → `status: "invited"`, `invited_at` set to now

Returns `409 Conflict` if the user is already a participant.
""",
)
async def join_event_route(
    org_id: str,
    event_id: str,
    body: ParticipantJoinRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
) -> ParticipantActionResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}/participants"):
        await join_event(
            session=db, organization_id=org_id, event_id=event_id,
            user_id=body.user_id, is_invite=body.is_invite,
        )
        await db.commit()
        return ParticipantActionResponse(event_id=event_id, user_id=body.user_id, message="Joined event.")


# ── Update RSVP status ────────────────────────────────────────────────────────

@router.patch(
    "/{event_id}/participants/{user_id}/status",
    response_model=ParticipantActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a participant's RSVP status",
    description="""
Update the RSVP status of a participant. Typically called by the participant
themselves in response to an invitation, or by an organizer correcting a status.

**Who can call this:**
Any authenticated member of the organization. No special permission required.
(If you want this restricted to the participant themselves or an admin, request that.)

**Path parameters:**
| Parameter | Notes |
|-----------|-------|
| `user_id` | UUID of the participant whose status is being updated. |

**Request body:**
| Field    | Type   | Required | Allowed values |
|----------|--------|----------|----------------|
| `status` | string | ✅       | `invited` \| `accepted` \| `declined` \| `pending` |

**Status meanings:**
- `pending`  — joined but hasn't responded yet (default for self-joins)
- `invited`  — formally invited, awaiting response
- `accepted` — confirmed attendance
- `declined` — declined the invitation

Returns `404` if the user is not a participant in this event.
""",
)
async def update_participant_status_route(
    org_id: str,
    event_id: str,
    user_id: str,
    body: ParticipantStatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
) -> ParticipantActionResponse:
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}/participants/{user_id}/status"):
        await update_participant_status(
            session=db, organization_id=org_id, event_id=event_id, user_id=user_id, new_status=body.status,
        )
        await db.commit()
        return ParticipantActionResponse(event_id=event_id, user_id=user_id, message="RSVP status updated.")


# ── Mark attendance ───────────────────────────────────────────────────────────

@router.patch(
    "/{event_id}/participants/{user_id}/attendance",
    response_model=ParticipantActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark a participant's attendance",
    description="""
Records whether a participant actually attended the event after it has taken place.
This is a post-event organizer action — it is separate from RSVP status, which
reflects intent *before* the event.

**Who can call this:**
Requires the `event.update` permission scoped to the event's branch (or org-wide).
This is intentionally restricted to organizers — participants cannot mark their
own attendance.

**Path parameters:**
| Parameter | Notes |
|-----------|-------|
| `user_id` | UUID of the participant to mark attendance for. |

**Request body:**
| Field      | Type    | Required | Notes |
|------------|---------|----------|-------|
| `attended` | boolean | ✅       | `true` = participant was present. `false` = no-show. |

**Note:** The `attended` flag is independent of RSVP `status`. A participant
can have `status: "accepted"` but `attended: false` (no-show), or
`status: "declined"` but `attended: true` (walk-in).

Returns `404` if the user is not a participant in this event.
""",
)
async def mark_attendance_route(
    org_id: str,
    event_id: str,
    user_id: str,
    body: ParticipantAttendanceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
    _: CurrentUser = Depends(require_permission("event.update")),
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


# ── Remove participant ────────────────────────────────────────────────────────

@router.delete(
    "/{event_id}/participants/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a participant or leave an event",
    description="""
Removes a user from the event's participant list. Supports two use cases:

1. **Self-removal** — a user leaving an event they joined.
2. **Admin removal** — an organizer removing someone else.

**Who can call this:**
Any authenticated member of the organization. No special permission is currently
required — the service layer does not yet restrict self-removal vs admin-removal.
(If you want admin-removal gated behind `event.update`, request that.)

**Path parameters:**
| Parameter | Notes |
|-----------|-------|
| `user_id` | UUID of the participant to remove. |

**Note:** This only removes the participation record. It does not affect the
user's account, branch membership, or any other data. The event itself is
unaffected.

Returns `204 No Content` on success. Returns `404` if the user is not
a participant in this event.
""",
)
async def remove_participant_route(
    org_id: str,
    event_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_active_subscription),
):
    require_same_organization(org_id, current_user)

    async with request_guard(db, "/organizations/{org_id}/events/{event_id}/participants/{user_id}"):
        await remove_participant(session=db, organization_id=org_id, event_id=event_id, user_id=user_id)
        await db.commit()