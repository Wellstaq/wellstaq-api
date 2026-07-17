"""
app/services/challenge/progress_service.py

Ingestion + completion evaluation for challenge progress. See model
docstring on ChallengeProgressEntry for the day-boundary rationale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChallengeParticipant, ChallengeProgressEntry
from app.services.challenge._helpers import get_challenge_or_404
from app.services.notification import create_notification

logger = logging.getLogger(__name__)

WAT = ZoneInfo("Africa/Lagos")


def _today_wat() -> "date":
    return datetime.now(timezone.utc).astimezone(WAT).date()


async def submit_progress(
    *, session: AsyncSession, organization_id: str, challenge_id: str, user_id: str, value: float, source: str = "manual"
) -> ChallengeProgressEntry:
    """
    Upserts today's (WAT) entry for this participant, then checks for
    completion. entry_date is always computed server-side — never
    accepted from the client, to prevent a manipulated date being used
    to game daily_minimum challenges or backdate cumulative totals.
    """
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    participant_result = await session.execute(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id, ChallengeParticipant.user_id == user_id
        )
    )
    participant = participant_result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You must join this challenge before submitting progress.")

    if participant.completed_at is not None:
        return None  # already completed — ignore further submissions silently

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
        entry.value = value  # re-sync for today overwrites, doesn't duplicate
        entry.source = source
    else:
        entry = ChallengeProgressEntry(
            challenge_id=challenge_id, user_id=user_id, organization_id=organization_id,
            entry_date=entry_date, value=value, source=source,
        )
        session.add(entry)

    await session.flush()

    await _check_completion(session=session, challenge=challenge, participant=participant)

    return entry


async def _check_completion(*, session: AsyncSession, challenge, participant: ChallengeParticipant) -> None:
    """
    cumulative: sum ALL entries so far, complete the instant it crosses
    target_value — doesn't wait for end_date.

    daily_minimum: complete only once the challenge's own window has
    closed (end_date reached) AND every day in the window has an entry
    clearing target_value. Can't be determined mid-challenge, since a
    missed day tomorrow could still fail it.
    """
    if challenge.target_value is None:
        return  # no target set — nothing to auto-complete against

    if challenge.target_type == "cumulative":
        total_result = await session.execute(
            select(func.sum(ChallengeProgressEntry.value)).where(
                ChallengeProgressEntry.challenge_id == challenge.id,
                ChallengeProgressEntry.user_id == participant.user_id,
            )
        )
        total = total_result.scalar_one() or 0
        if total >= challenge.target_value:
            await _mark_complete(session=session, challenge=challenge, participant=participant)

    elif challenge.target_type == "daily_minimum":
        # TODO(background-job): this check only runs when the user submits
        # progress, so a "did every day hit the minimum" evaluation is
        # incomplete until the challenge's end_date job does the
        # authoritative final pass across ALL days in the window (including
        # days with no entry at all, which should count as a fail). This
        # inline check is an early/optimistic signal only, not the source
        # of truth for daily_minimum challenges — the scheduled job at
        # end_date is what actually finalizes these.
        pass


async def _mark_complete(*, session: AsyncSession, challenge, participant: ChallengeParticipant) -> None:
    participant.completed_at = datetime.now(timezone.utc)
    await session.flush()

    await create_notification(
        session=session,
        user_id=participant.user_id,
        organization_id=challenge.organization_id,
        notification_type="challenge_completed",
        reference_id=challenge.id,
        reference_type="challenge",
        body=f"You completed the '{challenge.name}' challenge!",
    )

    logger.info("Challenge auto-completed", extra={"challenge_id": challenge.id, "user_id": participant.user_id})