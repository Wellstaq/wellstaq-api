"""
app/services/engagement/priority_service.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserPriority
from app.models.activity import PRIORITY_VOCABULARY


async def set_priorities(*, session: AsyncSession, user_id: str, priorities: list[str]) -> list[UserPriority]:
    invalid = [p for p in priorities if p not in PRIORITY_VOCABULARY]
    if invalid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid priority value(s) {invalid} — must be one of {PRIORITY_VOCABULARY}.",
        )

    if len(set(priorities)) != len(priorities):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Duplicate priorities are not allowed.")

    await session.execute(delete(UserPriority).where(UserPriority.user_id == user_id))

    rows = []
    for index, priority in enumerate(priorities, start=1):
        row = UserPriority(user_id=user_id, priority=priority, rank=index)
        session.add(row)
        rows.append(row)

    await session.flush()
    for row in rows:
        await session.refresh(row)
    return rows


async def get_priorities(*, session: AsyncSession, user_id: str) -> list[UserPriority]:
    result = await session.execute(
        select(UserPriority).where(UserPriority.user_id == user_id).order_by(UserPriority.rank.asc())
    )
    return list(result.scalars().all())