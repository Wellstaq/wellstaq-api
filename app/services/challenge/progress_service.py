"""
app/services/challenge/progress_service.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChallengeParticipant, ChallengeProgressEntry
from app.services.challenge._helpers import check_challenge_completion, get_challenge_or_404

logger = logging.getLogger(__name__)

WAT = ZoneInfo("Africa/Lagos")


def _today_wat():
    return datetime.now(timezone.utc).astimezone(WAT).date()

async def submit_progress(
    *, session: AsyncSession, organization_id: str, challenge_id: str, user_id: str, value: float, source: str = "manual"
) -> ChallengeProgressEntry | None:
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    if challenge.status != "active":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This challenge is not currently active (status='{challenge.status}') — progress cannot be submitted.",
        )

    participant_result = await session.execute(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id, ChallengeParticipant.user_id == user_id
        )
    )
    participant = participant_result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You must join this challenge before submitting progress.")

    if participant.completed_at is not None:
        return None

    
    entry_date = _today_wat()

    existing = await session.execute(
        select(ChallengeProgressEntry).where(
            ChallengeProgressEntry.challenge_id == challenge_id,
            ChallengeProgressEntry.user_id == user_id,
            ChallengeProgressEntry.entry_date == entry_date,
        )
    )
    entry = existing.scalar_one_or_none()
    if entry is not None:
        entry.value = value
        entry.source = source
    else:
        entry = ChallengeProgressEntry(
            challenge_id=challenge_id, user_id=user_id, organization_id=organization_id,
            entry_date=entry_date, value=value, source=source,
        )
        session.add(entry)

    await session.flush()
    await check_challenge_completion(session=session, challenge=challenge, participant=participant)

    return entry