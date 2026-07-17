"""
app/services/challenge/delete_challenge.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select

from app.models import ChallengeParticipant
from app.services.challenge._helpers import get_challenge_or_404
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def delete_challenge(*, session: AsyncSession, organization_id: str, challenge_id: str) -> None:
    """
    Hard delete — cascades to ChallengeParticipant and
    ChallengeProgressEntry via FK ondelete. Refuses to run if any
    participant has already completed the challenge (completed_at set),
    since that's a personal achievement record worth preserving. Use
    cancel_challenge instead for a challenge with completions — it
    preserves all participant/progress history while stopping further
    activity, same pattern as Event's cancel vs delete split.
    """
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    completed_count_result = await session.execute(
        select(func.count()).select_from(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id,
            ChallengeParticipant.completed_at.is_not(None),
        )
    )
    completed_count = completed_count_result.scalar_one()

    if completed_count > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot delete this challenge — {completed_count} participant(s) have already completed it. "
            "Cancel it instead to preserve their records.",
        )

    await session.delete(challenge)
    await session.flush()
    logger.info("Challenge deleted", extra={"challenge_id": challenge_id})