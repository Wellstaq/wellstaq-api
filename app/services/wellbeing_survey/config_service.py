"""
app/services/wellbeing_survey/config_service.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wellbeing_survey import SURVEY_CADENCES, WellbeingSurveyConfig


async def set_survey_cadence(*, session: AsyncSession, organization_id: str, cadence: str) -> WellbeingSurveyConfig:
    if cadence not in SURVEY_CADENCES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"cadence must be one of {SURVEY_CADENCES}.")

    result = await session.execute(
        select(WellbeingSurveyConfig).where(WellbeingSurveyConfig.organization_id == organization_id)
    )
    config = result.scalar_one_or_none()

    if config is not None:
        config.cadence = cadence
    else:
        config = WellbeingSurveyConfig(organization_id=organization_id, cadence=cadence, is_active=True)
        session.add(config)

    await session.flush()
    await session.refresh(config)
    return config


async def get_survey_config(*, session: AsyncSession, organization_id: str) -> WellbeingSurveyConfig | None:
    result = await session.execute(
        select(WellbeingSurveyConfig).where(WellbeingSurveyConfig.organization_id == organization_id)
    )
    return result.scalar_one_or_none()