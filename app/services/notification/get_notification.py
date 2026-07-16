"""
app/services/notification/get_notifications.py
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification


async def get_notifications(
    *, session: AsyncSession, user_id: str, unread_only: bool = False, offset: int = 0, limit: int = 50
) -> tuple[list[Notification], int, int]:
    """Returns (notifications, total matching the filter, unread_count
    regardless of filter) — unread_count is always the true unread total,
    useful for a badge even when the caller is viewing "all" notifications."""
    query = select(Notification).where(Notification.user_id == user_id)
    count_query = select(func.count()).select_from(Notification).where(Notification.user_id == user_id)

    if unread_only:
        query = query.where(Notification.is_read.is_(False))
        count_query = count_query.where(Notification.is_read.is_(False))

    query = query.order_by(Notification.created_at.desc()).offset(offset).limit(limit)

    unread_count_query = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
    )

    total = (await session.execute(count_query)).scalar_one()
    unread_count = (await session.execute(unread_count_query)).scalar_one()
    notifications = (await session.execute(query)).scalars().all()
    return list(notifications), total, unread_count


async def get_unread_count(*, session: AsyncSession, user_id: str) -> int:
    query = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
    )
    return (await session.execute(query)).scalar_one()