"""
app/jobs/challenges/lifecycle.py

activate_and_close_challenges — status transitions (draft->active,
active->completed), AND the authoritative daily_minimum completion
evaluation, which can only run once a challenge's window has fully
closed (a single passing day doesn't mean the whole challenge is won —
a later day could still fail, so this can't be determined mid-challenge
the way cumulative challenges are).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from app.core.database import get_db
from app.models import Challenge, ChallengeParticipant, ChallengeProgressEntry
from app.services.challenge._helpers import mark_challenge_complete

logger = logging.getLogger(__name__)


async def activate_and_close_challenges(ctx: dict[str, Any]) -> None:
    today = date.today()
    activated = 0
    completed = 0
    daily_minimum_evaluated = 0
    daily_minimum_passed = 0

    async for session in get_db():
        draft_result = await session.execute(
            select(Challenge).where(
                Challenge.status == "draft",
                Challenge.start_date.is_not(None),
                Challenge.start_date <= today,
            )
        )
        for challenge in draft_result.scalars().all():
            challenge.status = "active"
            activated += 1

        active_result = await session.execute(
            select(Challenge).where(
                Challenge.status == "active",
                Challenge.end_date.is_not(None),
                Challenge.end_date < today,
            )
        )
        closing_challenges = active_result.scalars().all()

        for challenge in closing_challenges:
            challenge.status = "completed"
            completed += 1

            if challenge.target_type == "daily_minimum" and challenge.target_value is not None:
                passed_count = await _finalize_daily_minimum_challenge(session, challenge)
                daily_minimum_evaluated += 1
                daily_minimum_passed += passed_count

        await session.commit()
        break

    logger.info(
        "Challenge status transitions applied",
        extra={
            "activated": activated, "completed": completed,
            "daily_minimum_challenges_evaluated": daily_minimum_evaluated,
            "daily_minimum_participants_passed": daily_minimum_passed,
        },
    )


async def _finalize_daily_minimum_challenge(session, challenge: Challenge) -> int:
    """
    Walks every day in [start_date, end_date] for every participant who
    hasn't already been marked complete, and checks that EVERY day has
    an entry meeting target_value — a day with NO entry counts as a
    fail (missing = didn't meet the minimum that day), same as a day
    with a value below target_value. Only participants who pass every
    single day get marked complete (and notified, via
    _mark_challenge_complete).

    Returns the count of participants who passed.
    """
    if challenge.start_date is None or challenge.end_date is None:
        return 0

    required_days = set()
    cursor = challenge.start_date
    while cursor <= challenge.end_date:
        required_days.add(cursor)
        cursor += timedelta(days=1)

    participants_result = await session.execute(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge.id,
            ChallengeParticipant.completed_at.is_(None),
        )
    )
    participants = participants_result.scalars().all()

    passed_count = 0
    for participant in participants:
        entries_result = await session.execute(
            select(ChallengeProgressEntry.entry_date, ChallengeProgressEntry.value).where(
                ChallengeProgressEntry.challenge_id == challenge.id,
                ChallengeProgressEntry.user_id == participant.user_id,
            )
        )
        entries_by_date = {entry_date: value for entry_date, value in entries_result.all()}

        passed_every_day = all(
            entries_by_date.get(day, 0) >= challenge.target_value for day in required_days
        )

        if passed_every_day:
            await _mark_challenge_complete(session=session, challenge=challenge, participant=participant)
            passed_count += 1

    return passed_count