"""
app/routers/notifications.py

Every route here is scoped to current_user.user_id — no permission
system, no branch checks. A user only ever sees, marks, or deletes their
OWN notifications. There is deliberately no POST route — notifications
are created as a side effect by other services (see
app/services/notification/create_notification.py), never directly by a
user hitting the API.

Routes:
  GET    /notifications                    — list mine (optional ?unread_only=)
  GET    /notifications/unread-count        — badge count
  PATCH  /notifications/{notification_id}/read — mark one as read
  PATCH  /notifications/read-all             — mark all as read
  DELETE /notifications/{notification_id}    — delete one of mine
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.guards import request_guard
from app.schemas.notification import (
    MarkAllReadResponse,
    MarkReadResponse,
    NotificationListResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from app.services.notification import (
    delete_notification,
    get_notifications,
    get_unread_count,
    mark_all_as_read,
    mark_as_read,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse, status_code=status.HTTP_200_OK)
async def list_notifications_route(
    unread_only: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> NotificationListResponse:
    async with request_guard(db, "/notifications"):
        notifications, total, unread_count = await get_notifications(
            session=db, user_id=current_user.user_id, unread_only=unread_only, offset=offset, limit=limit,
        )
        return NotificationListResponse(
            items=[NotificationResponse.model_validate(n) for n in notifications],
            total=total, unread_count=unread_count, offset=offset, limit=limit,
        )


@router.get("/unread-count", response_model=UnreadCountResponse, status_code=status.HTTP_200_OK)
async def unread_count_route(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> UnreadCountResponse:
    async with request_guard(db, "/notifications/unread-count"):
        count = await get_unread_count(session=db, user_id=current_user.user_id)
        return UnreadCountResponse(unread_count=count)


@router.patch("/{notification_id}/read", response_model=MarkReadResponse, status_code=status.HTTP_200_OK)
async def mark_notification_read_route(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MarkReadResponse:
    async with request_guard(db, "/notifications/{notification_id}/read"):
        notification = await mark_as_read(session=db, user_id=current_user.user_id, notification_id=notification_id)
        await db.commit()
        return MarkReadResponse(notification_id=notification.id, is_read=True, message="Marked as read.")


@router.patch("/read-all", response_model=MarkAllReadResponse, status_code=status.HTTP_200_OK)
async def mark_all_notifications_read_route(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MarkAllReadResponse:
    async with request_guard(db, "/notifications/read-all"):
        count = await mark_all_as_read(session=db, user_id=current_user.user_id)
        await db.commit()
        return MarkAllReadResponse(marked_count=count, message=f"{count} notification(s) marked as read.")


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_route(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    async with request_guard(db, "/notifications/{notification_id}"):
        await delete_notification(session=db, user_id=current_user.user_id, notification_id=notification_id)
        await db.commit()