"""
app/services/activity_log/submit_activity.py

Single ingestion point for a user's daily metrics (steps, distance,
etc.) — feeds THREE downstream consumers from one submission:
  1. ActivityLog itself — the general "my daily steps" record, shown
     regardless of challenge participation (Average Daily Steps,
     Monthly/Weekly Trend screens).
  2. ChallengeProgressEntry — for every ACTIVE challenge the user is
     currently participating in whose metric_type matches this
     submission. This replaces the old challenge-only submit_progress
     endpoint — a user now submits their steps ONCE, and it counts
     toward both their general activity history AND any matching
     challenge automatically. See services/challenge/progress_service.py
     for the completion-check logic this delegates to.
  3. (Indirectly) LeaderboardSnapshot / DepartmentRankSnapshot — these
     are NOT touched here; they're computed later by the scheduled job
     reading ActivityLog rows, not updated live on every submission.

One entry per (user, metric_type, date) — a same-day resubmission
UPDATES the existing row rather than creating a duplicate, mirroring
ChallengeProgressEntry's own upsert behavior.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog, Challenge, ChallengeParticipant
from app.services.activity_log._helpers import today_utc
from app.services.challenge._helpers import check_challenge_completion

logger = logging.getLogger(__name__)


async def submit_activity(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str,
    user_id: str,
    metric_type: str,
    value,
    unit: str | None,
    source: str = "manual",
) -> tuple[ActivityLog, list[str]]:
    normalized_metric_type = metric_type.strip().lower()
    today = today_utc()

    existing = await session.execute(
        select(ActivityLog).where(
            ActivityLog.user_id == user_id,
            ActivityLog.metric_type == normalized_metric_type,
            ActivityLog.date == today,
        )
    )
    entry = existing.scalar_one_or_none()

    if entry is not None:
        entry.value = value
        entry.unit = unit
    else:
        entry = ActivityLog(
            organization_id=organization_id,
            branch_id=branch_id,
            user_id=user_id,
            metric_type=normalized_metric_type,
            value=value,
            unit=unit,
            date=today,
        )
        session.add(entry)

    await session.flush()

    challenges_updated = aawait check_challenge_completion(session=session, challenge=challenge, participant=participant)

    logger.info(
        "Activity submitted",
        extra={"user_id": user_id, "metric_type": normalized_metric_type, "value": float(value), "challenges_updated": challenges_updated},
    )
    return entry, challenges_updated


async def _fanout_to_active_challenges(
    *, session: AsyncSession, organization_id: str, user_id: str, metric_type: str, value
) -> list[str]:
    """
    Finds every challenge this user is an active (not yet completed)
    participant of, where Challenge.metric_type matches this
    submission's metric_type, and upserts today's ChallengeProgressEntry
    for each — same duplication-avoidance logic as
    progress_service.submit_progress, just triggered from here instead
    of a challenge-specific endpoint.
    """
    from app.models import ChallengeProgressEntry
    from app.services.challenge._helpers import get_challenge_or_404
    from app.services.activity_log._helpers import today_utc

    result = await session.execute(
        select(Challenge, ChallengeParticipant)
        .join(ChallengeParticipant, ChallengeParticipant.challenge_id == Challenge.id)
        .where(
            ChallengeParticipant.user_id == user_id,
            ChallengeParticipant.completed_at.is_(None),
            Challenge.organization_id == organization_id,
            Challenge.metric_type == metric_type,
            Challenge.status == "active",
        )
    )
    rows = result.all()
    updated_ids: list[str] = []
    today = today_utc()

    for challenge, participant in rows:
        existing = await session.execute(
            select(ChallengeProgressEntry).where(
                ChallengeProgressEntry.challenge_id == challenge.id,
                ChallengeProgressEntry.user_id == user_id,
                ChallengeProgressEntry.entry_date == today,
            )
        )
        progress_entry = existing.scalar_one_or_none()
        if progress_entry is not None:
            progress_entry.value = value
            progress_entry.source = "activity_log"
        else:
            progress_entry = ChallengeProgressEntry(
                challenge_id=challenge.id, user_id=user_id, organization_id=organization_id,
                entry_date=today, value=value, source="activity_log",
            )
            session.add(progress_entry)

        await session.flush()
        await _check_completion(session=session, challenge=challenge, participant=participant)
        updated_ids.append(challenge.id)

    return updated_ids