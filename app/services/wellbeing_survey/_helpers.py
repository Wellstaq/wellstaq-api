"""
app/services/wellbeing_survey/_helpers.py
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wellbeing_survey import WellbeingSurveyWindow

CADENCE_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}


async def get_open_window_or_404(*, session: AsyncSession, organization_id: str) -> WellbeingSurveyWindow:
    result = await session.execute(
        select(WellbeingSurveyWindow).where(
            WellbeingSurveyWindow.organization_id == organization_id,
            WellbeingSurveyWindow.is_closed.is_(False),
        )
        .order_by(WellbeingSurveyWindow.window_start.desc())
        .limit(1)
    )
    window = result.scalar_one_or_none()
    if window is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open wellbeing survey window for this organization.")
    return window


def compute_window_range(*, cadence: str, start: date) -> tuple[date, date]:
    days = CADENCE_DAYS[cadence]
    return start, start + timedelta(days=days - 1)