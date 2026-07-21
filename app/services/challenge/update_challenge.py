"""
app/services/challenge/update_challenge.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from app.services.challenge._helpers import get_challenge_or_404
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.constants import CHALLENGE_STATUSES, TARGET_TYPES


logger = logging.getLogger(__name__)



async def edit_challenge(
    *,
    session: AsyncSession,
    organization_id: str,
    challenge_id: str,
    name: str | None,
    description: str | None,
    start_date,
    end_date,
    metric_type: str | None,
    target_type: str | None,
    target_value,
    status_: str | None,
):
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    if name is not None:
        challenge.name = name.strip()
    if description is not None:
        challenge.description = description
    if start_date is not None:
        challenge.start_date = start_date
    if end_date is not None:
        challenge.end_date = end_date
    if metric_type is not None:
        # Same normalization as create_challenge — keep the two paths
        # consistent so a value written on create and one written on
        # edit can never diverge in casing.
        challenge.metric_type = metric_type.strip().lower()
    if target_type is not None:
        if target_type not in TARGET_TYPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid target_type '{target_type}'.")
        challenge.target_type = target_type
    if target_value is not None:
        challenge.target_value = target_value
    if status_ is not None:
        if status_ not in CHALLENGE_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid status '{status_}'.")
        challenge.status = status_

    if challenge.target_type == "daily_minimum" and challenge.end_date is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "daily_minimum challenges require an end_date.",
        )

    await session.flush()
    await session.refresh(challenge)
    logger.info("Challenge updated", extra={"challenge_id": challenge_id})
    return challenge

async def cancel_challenge(*, session: AsyncSession, organization_id: str, challenge_id: str):
    """Preferred over delete_challenge when the challenge has completions
    or in-progress participants worth keeping. Preserves all
    ChallengeParticipant/ChallengeProgressEntry rows — only the parent
    challenge's status changes."""
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    if challenge.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "Challenge is already cancelled.")

    challenge.status = "cancelled"
    await session.flush()
    await session.refresh(challenge)
    logger.info("Challenge cancelled", extra={"challenge_id": challenge_id})
    return challenge