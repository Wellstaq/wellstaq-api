"""
app/services/challenge/submission_service.py

Evidence-based submissions (tracking_method='evidence_based') — submit,
admin review (approve/reject), and the per-challenge leaderboard.

A submission IS a ChallengeProgressEntry (value=1, source='evidence'); this
module just adds the evidence-specific lifecycle around it. Only entries
with review_status='approved' count toward progress — see
check_challenge_completion and get_participant_progress.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChallengeParticipant, ChallengeProgressEntry, User
from app.services.challenge._helpers import (
    check_challenge_completion,
    confirm_submission_photo,
    get_challenge_or_404,
    today_wat,
)
from app.services.notification import create_notification

logger = logging.getLogger(__name__)


async def submit_evidence(
    *, session: AsyncSession, organization_id: str, challenge_id: str, user_id: str,
    photo_url: str | None, note: str | None,
) -> ChallengeProgressEntry:
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    if challenge.tracking_method != "evidence_based":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This challenge does not use evidence-based tracking.")

    if challenge.evidence_required and not photo_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Photo evidence is required for this challenge.")

    if challenge.status != "active":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This challenge is not currently active (status='{challenge.status}') — progress cannot be submitted.",
        )

    confirm_submission_photo(photo_url)

    participant_result = await session.execute(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id, ChallengeParticipant.user_id == user_id
        )
    )
    participant = participant_result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You must join this challenge before submitting progress.")

    if participant.completed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already completed this challenge.")

    entry_date = today_wat()
    review_status = "pending" if challenge.requires_approval else "approved"

    existing = await session.execute(
        select(ChallengeProgressEntry).where(
            ChallengeProgressEntry.challenge_id == challenge_id,
            ChallengeProgressEntry.user_id == user_id,
            ChallengeProgressEntry.entry_date == entry_date,
        )
    )
    entry = existing.scalar_one_or_none()
    # A rejected same-day submission can be resubmitted; an already
    # pending/approved one cannot be overwritten by a second submission.
    if entry is not None and entry.review_status != "rejected":
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already submitted evidence for today.")

    if entry is not None:
        entry.photo_url = photo_url
        entry.note = note
        entry.review_status = review_status
        entry.rejection_reason = None
        entry.reviewed_by = None
        entry.reviewed_at = None
    else:
        entry = ChallengeProgressEntry(
            challenge_id=challenge_id, user_id=user_id, organization_id=organization_id,
            entry_date=entry_date, value=1, source="evidence",
            photo_url=photo_url, note=note, review_status=review_status,
        )
        session.add(entry)

    await session.flush()

    if review_status == "approved":
        await check_challenge_completion(session=session, challenge=challenge, participant=participant)

    logger.info(
        "Evidence submitted", extra={"challenge_id": challenge_id, "user_id": user_id, "review_status": review_status},
    )
    return entry


async def list_submissions(
    *, session: AsyncSession, organization_id: str, challenge_id: str,
    review_status: str | None = "pending", offset: int = 0, limit: int = 50,
) -> tuple[list[ChallengeProgressEntry], int]:
    await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    query = (
        select(ChallengeProgressEntry, User)
        .join(User, User.id == ChallengeProgressEntry.user_id)
        .where(ChallengeProgressEntry.challenge_id == challenge_id, ChallengeProgressEntry.source == "evidence")
        .order_by(ChallengeProgressEntry.entry_date.desc())
        .offset(offset)
        .limit(limit)
    )
    count_query = select(func.count()).select_from(ChallengeProgressEntry).where(
        ChallengeProgressEntry.challenge_id == challenge_id, ChallengeProgressEntry.source == "evidence",
    )
    if review_status is not None:
        query = query.where(ChallengeProgressEntry.review_status == review_status)
        count_query = count_query.where(ChallengeProgressEntry.review_status == review_status)

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query)).all()
    return list(rows), total


async def review_submission(
    *, session: AsyncSession, organization_id: str, challenge_id: str, entry_id: str,
    reviewer_id: str, action: str, reason: str | None = None,
) -> ChallengeProgressEntry:
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    result = await session.execute(
        select(ChallengeProgressEntry).where(
            ChallengeProgressEntry.id == entry_id,
            ChallengeProgressEntry.challenge_id == challenge_id,
            ChallengeProgressEntry.organization_id == organization_id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found.")

    if entry.review_status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Submission has already been {entry.review_status}.")

    entry.reviewed_by = reviewer_id
    entry.reviewed_at = datetime.now(timezone.utc)

    if action == "approve":
        entry.review_status = "approved"
        entry.rejection_reason = None
        await session.flush()

        participant_result = await session.execute(
            select(ChallengeParticipant).where(
                ChallengeParticipant.challenge_id == challenge_id, ChallengeParticipant.user_id == entry.user_id,
            )
        )
        participant = participant_result.scalar_one_or_none()
        if participant is not None:
            await check_challenge_completion(session=session, challenge=challenge, participant=participant)

        await create_notification(
            session=session, user_id=entry.user_id, organization_id=organization_id,
            notification_type="challenge_submission_approved", reference_id=challenge_id, reference_type="challenge",
            body=f"Your submission for '{challenge.name}' was approved.",
        )
    else:
        entry.review_status = "rejected"
        entry.rejection_reason = reason
        await session.flush()

        await create_notification(
            session=session, user_id=entry.user_id, organization_id=organization_id,
            notification_type="challenge_submission_rejected", reference_id=challenge_id, reference_type="challenge",
            body=f"Your submission for '{challenge.name}' was rejected: {reason}",
        )

    logger.info(
        "Submission reviewed",
        extra={"challenge_id": challenge_id, "entry_id": entry_id, "action": action, "reviewer_id": reviewer_id},
    )
    return entry


async def set_participant_score(
    *, session: AsyncSession, organization_id: str, challenge_id: str, user_id: str, score: int,
) -> ChallengeParticipant:
    await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    result = await session.execute(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id,
            ChallengeParticipant.user_id == user_id,
            ChallengeParticipant.organization_id == organization_id,
        )
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Participant not found.")

    participant.score = score
    await session.flush()
    await session.refresh(participant)
    return participant


async def get_leaderboard(
    *, session: AsyncSession, organization_id: str, challenge_id: str, offset: int = 0, limit: int = 50,
) -> tuple[list[dict], int]:
    """
    Ranking rule: participants with an admin-assigned score rank by score
    (highest first) — for competition-style challenges judged on
    submission quality (see spec's cooking-challenge scoring example).
    Everyone else ranks by approved progress value (highest first), then
    by who completed first. This mirrors get_participant_progress's
    'only approved entries count' rule.
    """
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    progress_subq = (
        select(
            ChallengeProgressEntry.user_id,
            func.sum(ChallengeProgressEntry.value).label("current_value"),
        )
        .where(ChallengeProgressEntry.challenge_id == challenge_id, ChallengeProgressEntry.review_status == "approved")
        .group_by(ChallengeProgressEntry.user_id)
        .subquery()
    )

    query = (
        select(ChallengeParticipant, User, progress_subq.c.current_value)
        .join(User, User.id == ChallengeParticipant.user_id)
        .outerjoin(progress_subq, progress_subq.c.user_id == ChallengeParticipant.user_id)
        .where(ChallengeParticipant.challenge_id == challenge_id)
        .order_by(
            ChallengeParticipant.score.desc().nulls_last(),
            progress_subq.c.current_value.desc().nulls_last(),
            ChallengeParticipant.completed_at.asc().nulls_last(),
        )
        .offset(offset)
        .limit(limit)
    )
    count_query = select(func.count()).select_from(ChallengeParticipant).where(ChallengeParticipant.challenge_id == challenge_id)

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query)).all()

    items = []
    for rank_offset, (participant, user, current_value) in enumerate(rows):
        value = current_value or 0
        percent_complete = None
        if challenge.target_type == "cumulative" and challenge.target_value:
            percent_complete = min(100, round((value / challenge.target_value) * 100, 2))
        items.append({
            "rank": offset + rank_offset + 1,
            "user_id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "score": participant.score,
            "completed_at": participant.completed_at,
            "current_value": value,
            "percent_complete": percent_complete,
        })

    return items, total
