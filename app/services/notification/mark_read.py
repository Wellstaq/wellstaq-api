"""
app/services/notification/mark_read.py
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification


def _read_timestamp() -> datetime:
    """Return UTC in the naive form used by notifications.read_at.

    The column predates the timezone-aware audit columns and is stored as
    ``TIMESTAMP WITHOUT TIME ZONE`` in Postgres. asyncpg rejects an aware
    datetime for that column, so normalize UTC before either update path.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def mark_as_read(*, session: AsyncSession, user_id: str, notification_id: str) -> Notification:
    result = await session.execute(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == user_id)
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found.")

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = _read_timestamp()
        await session.flush()

    return notification


async def mark_all_as_read(*, session: AsyncSession, user_id: str) -> int:
    """Bulk UPDATE rather than fetch-then-loop — cheaper for a user with
    many unread notifications, and avoids loading rows into memory just
    to flip two columns."""
    now = _read_timestamp()
    result = await session.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
        .values(is_read=True, read_at=now)
    )
    await session.flush()
    return result.rowcount
