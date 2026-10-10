"""
app/services/challenge/progress_service.py
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChallengeParticipant, ChallengeProgressEntry
from app.schemas.activity_log import AUTOMATIC_ACTIVITY_METRICS
from app.services.challenge._helpers import check_challenge_completion, get_challenge_or_404, today_wat

logger = logging.getLogger(__name__)

async def submit_progress(
    *, session: AsyncSession, organization_id: str, challenge_id: str, user_id: str, value: float, source: str = "manual"
) -> ChallengeProgressEntry | None:
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    if challenge.metric_type in AUTOMATIC_ACTIVITY_METRICS or challenge.tracking_method == "automatic":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Steps, running, and calories are updated automatically from the connected health provider.",
        )

    if challenge.tracking_method == "evidence_based":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This challenge requires photo evidence — submit via POST .../submissions instead.",
        )

    if challenge.tracking_method == "platform_recorded":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Wellstaq records participation in this challenge automatically — manual progress cannot be submitted.",
        )

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

    
    entry_date = today_wat()

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

async def get_participant_progress(
    *, session: AsyncSession, organization_id: str, challenge_id: str, user_id: str
) -> dict:
    """
    Returns current standing for one participant in one challenge,
    INCLUDING the full day-by-day breakdown (daily_entries) — essential
    for daily_minimum challenges, where a single summed total doesn't
    meaningfully represent progress (see target_type-specific notes
    below). Also returned for cumulative challenges, since seeing the
    daily contributions behind the total is useful there too.

    current_value: SUM of all ChallengeProgressEntry.value — the
    lifetime total logged so far, for either target_type.

    percent_complete: only meaningful for 'cumulative' — None for
    'daily_minimum', since "% of a target sum" doesn't represent
    pass/fail-per-day progress. Use days_met_target /
    (days elapsed so far) instead for daily_minimum's real completion
    picture.

    days_met_target: for 'daily_minimum' challenges, the count of days
    where that day's entry >= target_value — this IS the real progress
    metric for this challenge type, analogous to percent_complete for
    cumulative. None for 'cumulative' challenges (not a meaningful
    concept there).

    days_logged: total number of distinct days with ANY entry,
    regardless of target_type — useful for showing "you've logged N of
    M days" even before knowing whether each day passed.
    """
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    participant_result = await session.execute(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id, ChallengeParticipant.user_id == user_id
        )
    )
    participant = participant_result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You are not a participant of this challenge.")

    entries_result = await session.execute(
        select(ChallengeProgressEntry.entry_date, ChallengeProgressEntry.value, ChallengeProgressEntry.review_status)
        .where(ChallengeProgressEntry.challenge_id == challenge_id, ChallengeProgressEntry.user_id == user_id)
        .order_by(ChallengeProgressEntry.entry_date.asc())
    )
    entries = entries_result.all()

    # Only 'approved' entries count toward the official total/target check —
    # 'pending' (awaiting evidence review) and 'rejected' entries are still
    # shown per-day below so the participant can see their submission's
    # status, but they haven't earned progress yet.
    approved_entries = [(d, v) for d, v, review_status in entries if review_status == "approved"]
    current_value = sum((value for _date, value in approved_entries), start=0)
    days_logged = len(entries)

    daily_entries = []
    days_met_target = None
    if challenge.target_type == "daily_minimum" and challenge.target_value is not None:
        days_met_target = 0
        for entry_date, value, review_status in entries:
            met = review_status == "approved" and value >= challenge.target_value
            if met:
                days_met_target += 1
            daily_entries.append({"entry_date": entry_date, "value": value, "met_target": met, "review_status": review_status})
    else:
        for entry_date, value, review_status in entries:
            daily_entries.append({"entry_date": entry_date, "value": value, "met_target": None, "review_status": review_status})

    percent_complete = None
    if challenge.target_type == "cumulative" and challenge.target_value:
        percent_complete = min(100, round((current_value / challenge.target_value) * 100, 2))

    days_remaining = None
    if challenge.end_date is not None:
        remaining = (challenge.end_date - date.today()).days
        days_remaining = max(0, remaining)

    return {
        "challenge_id": challenge_id,
        "user_id": user_id,
        "target_type": challenge.target_type,
        "target_value": challenge.target_value,
        "current_value": current_value,
        "percent_complete": percent_complete,
        "completed_at": participant.completed_at,
        "days_remaining": days_remaining,
        "daily_entries": daily_entries,
        "days_met_target": days_met_target,
        "days_logged": days_logged,
    }
