"""
app/jobs/challenges/lifecycle.py

activate_and_close_challenges — status transitions (upcoming->active,
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

from sqlalchemy import select, func

from app.jobs.shared import get_job_session
from app.services.challenge._helpers import mark_challenge_complete
from app.models import (
    Challenge, ChallengeParticipant, ChallengeProgressEntry,
    ChallengeStatsSnapshot, Organization, Branch, User,
)


logger = logging.getLogger(__name__)


async def activate_and_close_challenges(ctx: dict[str, Any]) -> None:
    today = date.today()
    activated = 0
    completed = 0
    daily_minimum_evaluated = 0
    daily_minimum_passed = 0

    async with get_job_session() as session:
        upcoming_result = await session.execute(
            select(Challenge).where(
                Challenge.status == "upcoming",
                Challenge.start_date.is_not(None),
                Challenge.start_date <= today,
            )
        )
        for challenge in upcoming_result.scalars().all():
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

    logger.info(
        "Challenge status transitions applied",
        extra={
            "activated": activated, "completed": completed,
            "daily_minimum_challenges_evaluated": daily_minimum_evaluated,
            "daily_minimum_participants_passed": daily_minimum_passed,
        },
    )


async def _finalize_daily_minimum_challenge(session, challenge: Challenge) -> int:
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
            await mark_challenge_complete(session=session, challenge=challenge, participant=participant)
            passed_count += 1

    return passed_count




async def compute_challenge_stats_snapshots(ctx: dict[str, Any]) -> None:
    """
    Daily snapshot, one row per org (branch_id=None, scope='all') and
    one row per branch (branch_id=<id>, own-members-only) — mirrors
    get_challenge_stats's two modes exactly, so trend deltas are always
    comparing like-for-like.
    """
    today = date.today()
    written = 0

    async with get_job_session() as session:
        org_ids = (await session.execute(select(Organization.id))).scalars().all()

        for org_id in org_ids:
            # Org-wide row (scope=all): every challenge, every participant
            challenge_ids = (
                select(Challenge.id).where(Challenge.organization_id == org_id).subquery()
            )
            active_count = (await session.execute(
                select(func.count()).select_from(Challenge).where(
                    Challenge.organization_id == org_id, Challenge.status == "active",
                )
            )).scalar_one()
            total_p, completed_p = (await session.execute(
                select(func.count(ChallengeParticipant.id), func.count(ChallengeParticipant.completed_at))
                .where(ChallengeParticipant.challenge_id.in_(select(challenge_ids.c.id)))
            )).one()

            await _upsert_snapshot(session, org_id, None, today, active_count, total_p, completed_p)
            written += 1

            # Per-branch rows: challenges visible to that branch (own + org-wide),
            # participants restricted to that branch's own members
            branch_ids = (await session.execute(
                select(Branch.id).where(Branch.organization_id == org_id)
            )).scalars().all()

            for branch_id in branch_ids:
                visible_challenge_ids = (
                    select(Challenge.id).where(
                        Challenge.organization_id == org_id,
                        (Challenge.branch_id == branch_id) | (Challenge.branch_id.is_(None)),
                    ).subquery()
                )
                active_count = (await session.execute(
                    select(func.count()).select_from(Challenge).where(
                        Challenge.organization_id == org_id,
                        (Challenge.branch_id == branch_id) | (Challenge.branch_id.is_(None)),
                        Challenge.status == "active",
                    )
                )).scalar_one()
                total_p, completed_p = (await session.execute(
                    select(func.count(ChallengeParticipant.id), func.count(ChallengeParticipant.completed_at))
                    .select_from(ChallengeParticipant)
                    .join(User, User.id == ChallengeParticipant.user_id)
                    .where(
                        ChallengeParticipant.challenge_id.in_(select(visible_challenge_ids.c.id)),
                        User.branch_id == branch_id,
                    )
                )).one()

                await _upsert_snapshot(session, org_id, branch_id, today, active_count, total_p, completed_p)
                written += 1

        await session.commit()

    logger.info("Challenge stats snapshots written", extra={"rows_written": written})


async def _upsert_snapshot(session, org_id, branch_id, snapshot_date, active, total_p, completed_p):
    existing = await session.execute(
        select(ChallengeStatsSnapshot).where(
            ChallengeStatsSnapshot.organization_id == org_id,
            ChallengeStatsSnapshot.branch_id == branch_id,
            ChallengeStatsSnapshot.snapshot_date == snapshot_date,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = ChallengeStatsSnapshot(organization_id=org_id, branch_id=branch_id, snapshot_date=snapshot_date)
        session.add(row)
    row.active_challenges = active
    row.total_participants = total_p
    row.completed_participants = completed_p
