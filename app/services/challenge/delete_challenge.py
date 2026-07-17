"""
app/services/challenge/delete_challenge.py
"""

from __future__ import annotations

import logging

from app.services.challenge._helpers import get_challenge_or_404
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def delete_challenge(*, session: AsyncSession, organization_id: str, challenge_id: str) -> None:
    """Hard delete — cascades to ChallengeParticipant via FK ondelete."""
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)
    await session.delete(challenge)
    await session.flush()
    logger.info("Challenge deleted", extra={"challenge_id": challenge_id})