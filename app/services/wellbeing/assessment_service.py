"""
app/services/wellbeing/assessment_service.py

Fully personal — self-only, same privacy boundary as baseline_service.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserHealthAssessment
from app.models.category import HealthCategory
from app.core.constants import ASSESSMENT_STATUSES 


async def submit_assessment(
    *, session: AsyncSession, organization_id: str, user_id: str,
    category_id: str | None, risk_score, status_: str,
) -> UserHealthAssessment:
    if status_ not in ASSESSMENT_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {ASSESSMENT_STATUSES}.")

    if category_id is not None:
        category_result = await session.execute(select(HealthCategory).where(HealthCategory.id == category_id))
        if category_result.scalar_one_or_none() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Health category not found.")

    assessment = UserHealthAssessment(
        organization_id=organization_id, user_id=user_id, category_id=category_id,
        risk_score=risk_score, status=status_,
        assessed_at=datetime.now(timezone.utc) if status_ in ("completed", "reviewed") else None,
    )
    session.add(assessment)
    await session.flush()
    await session.refresh(assessment)
    return assessment


async def get_my_assessments(*, session: AsyncSession, user_id: str) -> list[UserHealthAssessment]:
    result = await session.execute(
        select(UserHealthAssessment).where(UserHealthAssessment.user_id == user_id).order_by(UserHealthAssessment.created_at.desc())
    )
    return list(result.scalars().all())