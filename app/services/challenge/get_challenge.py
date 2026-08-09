# app/services/challenge/get_challenge.py
from __future__ import annotations

from app.models import Challenge, ChallengeParticipant, User
from app.services.challenge._helpers import get_challenge_or_404, apply_challenge_visibility_filter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_challenge(*, session: AsyncSession, organization_id: str, challenge_id: str) -> Challenge:
    return await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)


async def get_challenges(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str | None = None,
    scope: str | None = None,
    status: str | None = None,
    viewer_branch_id: str | None = None,
    is_super_admin: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Challenge], int]:
    base = select(Challenge).where(Challenge.organization_id == organization_id)
    base = apply_challenge_visibility_filter(
        base, branch_id=branch_id, scope=scope,
        viewer_branch_id=viewer_branch_id, is_super_admin=is_super_admin,
    )
    if status is not None:
        base = base.where(Challenge.status == status)

    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar_one()

    page_ids_subq = base.order_by(Challenge.created_at.desc()).offset(offset).limit(limit).with_only_columns(Challenge.id).subquery()

    # Participant aggregate join — same rule as stats:
    # branch_id set + scope != "all" → restrict to THIS branch's members only,
    # even for org-wide challenge cards. scope == "all" → every participant counts.
    participant_agg = (
        select(
            ChallengeParticipant.challenge_id,
            func.count(ChallengeParticipant.id).label("participant_count"),
            func.count(ChallengeParticipant.completed_at).label("completed_count"),
        )
        .where(ChallengeParticipant.challenge_id.in_(select(page_ids_subq.c.id)))
    )
    if branch_id is not None and scope != "all":
        participant_agg = participant_agg.join(User, User.id == ChallengeParticipant.user_id).where(
            User.branch_id == branch_id
        )
    participant_agg = participant_agg.group_by(ChallengeParticipant.challenge_id).subquery()

    final_stmt = (
        select(Challenge, participant_agg.c.participant_count, participant_agg.c.completed_count)
        .outerjoin(participant_agg, participant_agg.c.challenge_id == Challenge.id)
        .where(Challenge.id.in_(select(page_ids_subq.c.id)))
        .order_by(Challenge.created_at.desc())
    )
    rows = (await session.execute(final_stmt)).all()

    challenges = []
    for challenge, participant_count, completed_count in rows:
        pc = participant_count or 0
        challenge.participant_count = pc
        challenge.completion_rate = round((completed_count / pc) * 100, 1) if pc else None
        challenges.append(challenge)

    return challenges, total