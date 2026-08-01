"""
app/services/user_setting/profile_service.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def update_profile(
    *, session: AsyncSession, user_id: str, first_name: str | None, last_name: str | None,
    country: str | None, state: str | None,
) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    if first_name is not None:
        user.first_name = first_name.strip()
    if last_name is not None:
        user.last_name = last_name.strip()
    if country is not None:
        user.country = country.strip()
    if state is not None:
        user.state = state.strip()

    await session.flush()
    await session.refresh(user)
    return user


async def get_profile(*, session: AsyncSession, user_id: str) -> User:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    return user