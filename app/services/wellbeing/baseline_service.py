"""
app/services/wellbeing/baseline_service.py

Fully personal — self-only, no admin/manager visibility. See
conversation history: this and UserHealthAssessment are the two tables
deliberately excluded from any aggregate or manager-facing view.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserWellbeingBaseline

BASELINE_DIMENSIONS = ("mood", "stress", "energy", "work_life_balance")
BASELINE_LEVELS = ("amazed", "stressed", "tired", "excited", "infuriated")


async def submit_baseline(
    *, session: AsyncSession, organization_id: str, user_id: str, dimension: str, level: str, reason: str | None
) -> UserWellbeingBaseline:
    if dimension not in BASELINE_DIMENSIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"dimension must be one of {BASELINE_DIMENSIONS}.")
    if level not in BASELINE_LEVELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"level must be one of {BASELINE_LEVELS}.")

    baseline = UserWellbeingBaseline(
        organization_id=organization_id, user_id=user_id, dimension=dimension, level=level, reason=reason,
    )
    session.add(baseline)
    await session.flush()
    await session.refresh(baseline)
    return baseline


async def get_my_baselines(*, session: AsyncSession, user_id: str) -> list[UserWellbeingBaseline]:
    result = await session.execute(
        select(UserWellbeingBaseline).where(UserWellbeingBaseline.user_id == user_id).order_by(UserWellbeingBaseline.recorded_at.desc())
    )
    return list(result.scalars().all())