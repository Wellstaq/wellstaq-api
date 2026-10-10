from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Challenge, ChallengeParticipant, User, ChallengeStatsSnapshot
from app.services.challenge._helpers import apply_challenge_visibility_filter
from datetime import date, timedelta


PERIOD_DAYS = {"week": 7, "month": 30, "six_months": 182}

async def get_challenge_stats(
    *, session, organization_id, branch_id, scope,
    viewer_branch_id, is_super_admin,
    period: str | None = None, start_date: date | None = None, end_date: date | None = None,
) -> dict:
    # ── current, live values (unchanged from before) ──
    challenge_ids_subq = apply_challenge_visibility_filter(
        select(Challenge.id, Challenge.status).where(Challenge.organization_id == organization_id),
        branch_id=branch_id, scope=scope, viewer_branch_id=viewer_branch_id, is_super_admin=is_super_admin,
    ).subquery()

    active_challenges = (await session.execute(
        select(func.count()).select_from(challenge_ids_subq).where(challenge_ids_subq.c.status == "active")
    )).scalar_one()
    completed_challenges = (await session.execute(
        select(func.count()).select_from(challenge_ids_subq).where(challenge_ids_subq.c.status == "completed")
    )).scalar_one()
    upcoming_challenges = (await session.execute(
        select(func.count()).select_from(challenge_ids_subq).where(challenge_ids_subq.c.status == "upcoming")
    )).scalar_one()

    participant_query = (
        select(func.count(ChallengeParticipant.id), func.count(ChallengeParticipant.completed_at))
        .select_from(ChallengeParticipant)
        .where(ChallengeParticipant.challenge_id.in_(select(challenge_ids_subq.c.id)))
    )
    if branch_id is not None and scope != "all":
        participant_query = participant_query.join(User, User.id == ChallengeParticipant.user_id).where(
            User.branch_id == branch_id
        )
    total_participants, completed = (await session.execute(participant_query)).one()
    completion_rate = round((completed / total_participants) * 100, 1) if total_participants else 0.0

    current = {
        "active_challenges": active_challenges,
        "total_participants": total_participants,
        "completion_rate": completion_rate,
        "completed_challenges": completed_challenges,
        "upcoming_challenges": upcoming_challenges,
    }

    # period=None (default) → all-time view, no baseline to compare against
    if period is None:
        return {**current, "active_challenges_trend": None, "total_participants_trend": None, "completion_rate_trend": None}

    # Resolve which past date to diff against
    if period == "custom":
        target_date = start_date
    else:
        days_back = {"week": 7, "month": 30, "six_months": 182}[period]
        target_date = date.today() - timedelta(days=days_back)

    snapshot_branch_id = None if (scope == "all" or branch_id is None) else branch_id
    past = (await session.execute(
        select(ChallengeStatsSnapshot)
        .where(
            ChallengeStatsSnapshot.organization_id == organization_id,
            ChallengeStatsSnapshot.branch_id == snapshot_branch_id,
            ChallengeStatsSnapshot.snapshot_date <= target_date,
        )
        .order_by(ChallengeStatsSnapshot.snapshot_date.desc())
        .limit(1)
    )).scalar_one_or_none()

    def trend(curr, prev):
        if prev is None:
            return None
        pct = round(((curr - prev) / prev) * 100, 1) if prev else (100.0 if curr else 0.0)
        return {"current": curr, "previous": prev, "change_pct": pct}

    prev_completion = (
        round((past.completed_participants / past.total_participants) * 100, 1)
        if past and past.total_participants else (0.0 if past else None)
    )

    return {
        **current,
        "active_challenges_trend": trend(active_challenges, past.active_challenges if past else None),
        "total_participants_trend": trend(total_participants, past.total_participants if past else None),
        "completion_rate_trend": trend(completion_rate, prev_completion),
    }