"""
app/services/notification/create_notification.py

Not exposed via any route — this is an internal helper other services
call as a side effect (e.g. like_post calls this after liking). There is
no POST /notifications route: notifications are always system-generated,
never created directly by a user hitting the API.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification
from app.models.notification import NOTIFICATION_TYPES, REFERENCE_TYPES

logger = logging.getLogger(__name__)


async def create_notification(
    *,
    session: AsyncSession,
    user_id: str,
    notification_type: str,
    body: str,
    organization_id: str | None = None,
    actor_id: str | None = None,
    reference_id: str | None = None,
    reference_type: str | None = None,
    image_url: str | None = None,
) -> Notification:
    """
    Fire-and-forget style: caller is responsible for its own transaction —
    this only adds + flushes, never commits. If the caller's action fails
    after this call, the notification insert rolls back with it (correct
    behavior: don't notify about something that didn't actually happen).

    ASSERTS notification_type/reference_type are valid at the Python
    level too (not just relying on the DB CheckConstraint) so a bad value
    fails fast with a clear message instead of a raw IntegrityError deep
    in a commit.

    Does NOT notify the actor about their own action — callers should
    check `actor_id != user_id` before calling this, same as you
    wouldn't notify someone "You liked your own post." This function
    doesn't enforce that itself since some notification types (e.g.
    event_reminder, announcement) have no actor at all.
    """
    if notification_type not in NOTIFICATION_TYPES:
        raise ValueError(f"Invalid notification_type '{notification_type}'.")
    if reference_type is not None and reference_type not in REFERENCE_TYPES:
        raise ValueError(f"Invalid reference_type '{reference_type}'.")
    if (reference_id is None) != (reference_type is None):
        raise ValueError("reference_id and reference_type must both be set, or both omitted.")

    notification = Notification(
        user_id=user_id,
        organization_id=organization_id,
        actor_id=actor_id,
        notification_type=notification_type,
        reference_id=reference_id,
        reference_type=reference_type,
        body=body[:500],  # hard-truncate to match the DB CheckConstraint, never let this be the failure point
        image_url=image_url,
        is_read=False,
        read_at=None,
    )
    session.add(notification)
    await session.flush()

    # TODO(redis/websocket): push a real-time notification here once
    # that infra exists, instead of relying purely on poll/refresh.

    logger.info(
        "Notification created",
        extra={"notification_id": notification.id, "user_id": user_id, "type": notification_type},
    )
    return notification