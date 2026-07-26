"""
app/services/wellbeing_survey/response_service.py
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wellbeing_survey import PULSE_QUESTION_KEYS, WellbeingSurveyResponse
from app.services.wellbeing_survey._helpers import get_open_window_or_404


async def submit_survey_response(
    *, session: AsyncSession, organization_id: str, user_id: str, answers: dict[str, int]
) -> list[WellbeingSurveyResponse]:
    """
    answers: {question_key: 1-5}. All 5 questions must be present —
    partial submissions aren't accepted, since the per-person combined
    score needs a complete set to be meaningful.
    """
    window = await get_open_window_or_404(session=session, organization_id=organization_id)

    missing = set(PULSE_QUESTION_KEYS) - answers.keys()
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Missing answers for: {sorted(missing)}.")

    for question_key, value in answers.items():
        if question_key not in PULSE_QUESTION_KEYS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown question_key '{question_key}'.")
        if not isinstance(value, int) or not (1 <= value <= 5):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{question_key}' must be an integer 1-5.")

    existing = await session.execute(
        select(WellbeingSurveyResponse.id).where(
            WellbeingSurveyResponse.window_id == window.id, WellbeingSurveyResponse.user_id == user_id,
        ).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already responded to this window's survey.")

    responses = []
    for question_key, value in answers.items():
        response = WellbeingSurveyResponse(
            window_id=window.id, user_id=user_id, organization_id=organization_id,
            question_key=question_key, response_value=value,
        )
        session.add(response)
        responses.append(response)

    await session.flush()
    return responses


async def get_my_responses(*, session: AsyncSession, user_id: str, limit: int = 50) -> list[WellbeingSurveyResponse]:
    result = await session.execute(
        select(WellbeingSurveyResponse).where(WellbeingSurveyResponse.user_id == user_id)
        .order_by(WellbeingSurveyResponse.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())