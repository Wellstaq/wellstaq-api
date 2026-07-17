"""
app/services/challenge/participant_service.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser
from app.models import ChallengeParticipant, User
from app.services.challenge._helpers import check_participation_eligibility, get_challenge_or_404

logger = logging.getLogger(__name__)


async def join_challenge(
    *, session: AsyncSession, organization_id: str, challenge_id: str, current_user: CurrentUser
) -> ChallengeParticipant:
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)
    check_participation_eligibility(current_user=current_user, challenge=challenge)

    existing = await session.execute(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id, ChallengeParticipant.user_id == current_user.user_id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already joined this challenge.")

    participant = ChallengeParticipant(
        challenge_id=challenge_id, user_id=current_user.user_id, organization_id=organization_id,
    )
    session.add(participant)
    await session.flush()
    await session.refresh(participant)
    return participant

async def leave_challenge(*, session: AsyncSession, organization_id: str, challenge_id: str, current_user: CurrentUser) -> None:
    result = await session.execute(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == challenge_id,
            ChallengeParticipant.user_id == current_user.user_id,
            ChallengeParticipant.organization_id == organization_id,
        )
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You are not a participant of this challenge.")

    if participant.completed_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You cannot leave a challenge you have already completed — this preserves your completion record.",
        )

    await session.delete(participant)
    await session.flush()

async def get_participants(
    *, session: AsyncSession, organization_id: str, challenge_id: str, offset: int = 0, limit: int = 50
) -> tuple[list[tuple[ChallengeParticipant, User]], int]:
    await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    query = (
        select(ChallengeParticipant, User)
        .join(User, User.id == ChallengeParticipant.user_id)
        .where(ChallengeParticipant.challenge_id == challenge_id)
        .order_by(ChallengeParticipant.rank.asc().nulls_last(), ChallengeParticipant.joined_at.asc())
        .offset(offset)
        .limit(limit)
    )
    count_query = select(func.count()).select_from(ChallengeParticipant).where(ChallengeParticipant.challenge_id == challenge_id)

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query)).all()
    return list(rows), total


async def mark_participant_complete(
    *, session: AsyncSession, organization_id: str, challenge_id: str, user_id: str, rank: int | None
) -> ChallengeParticipant:
    """
    Manual admin override, gated by challenge.update at the router level.

    TODO(background-job): the real completion/ranking mechanism should be
    a scheduled job that reads each participant's progress against
    Challenge.metric_type (e.g. steps logged, wellbeing score delta) and
    sets completed_at/rank automatically — there is no metric-aggregation
    engine built yet, so this manual endpoint is a stopgap. Once that job
    exists, this function likely becomes an internal helper the job calls
    rather than something exposed for manual admin use.
    """
    from datetime import datetime, timezone

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

    participant.completed_at = datetime.now(timezone.utc)
    if rank is not None:
        participant.rank = rank

    await session.flush()
    await session.refresh(participant)
    return participant