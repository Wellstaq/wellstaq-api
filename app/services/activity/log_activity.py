"""
app/services/activity/log_activity.py

Internal helper — not exposed via any route. Other services call this as
a side effect (e.g. join_club, create_post) to record an entry in the
unified activity feed. Mirrors create_notification's pattern: caller
owns the transaction, this only adds + flushes.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserActivityFeed

logger = logging.getLogger(__name__)


async def log_activity(
    *,
    session: AsyncSession,
    user_id: str,
    organization_id: str,
    activity_type: str,
    reference_id: str | None = None,
    reference_type: str | None = None,
    metadata: dict | None = None,
) -> UserActivityFeed:
    if (reference_id is None) != (reference_type is None):
        raise ValueError("reference_id and reference_type must both be set, or both omitted.")

    activity = UserActivityFeed(
        user_id=user_id,
        organization_id=organization_id,
        activity_type=activity_type,
        reference_id=reference_id,
        reference_type=reference_type,
        activity_metadata=metadata,
    )
    session.add(activity)
    await session.flush()

    logger.info("Activity logged", extra={"activity_id": activity.id, "user_id": user_id, "type": activity_type})
    return activity