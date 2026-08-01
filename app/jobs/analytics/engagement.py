"""
app/jobs/analytics/leaderboard.py

compute_leaderboard_snapshots recomputes LeaderboardSnapshot rows for
every organization and metric_type with activity in the requested
period, computing both branch-local and organization-wide rankings.

period_type ('daily' | 'weekly' | 'monthly') distinguishes snapshot
granularity so identical date ranges cannot be confused between
different reporting periods. The same function can therefore be reused
for daily, weekly, monthly, or manual backfill jobs.

The job is idempotent: existing snapshots for the same
(organization_id, user_id, metric_type, period_type, period_start,
period_end) are updated instead of duplicated, assuming the model
enforces the corresponding UniqueConstraint.

Performance: all reads are bulk queries (no per-user database queries).
Ranking is performed entirely in memory. Remaining tradeoffs are the
per-organization/per-metric iteration and one ORM write per snapshot
row, both acceptable until larger production scale requires further
optimization.

"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from app.jobs.helpers.scoring import cap_score, trend_direction
from app.jobs.shared import get_job_session
from app.models import (
    ActivityLog,
    ChallengeParticipant,
    ClubMember,
    DailyCheckin,
    EngagementLog,
    Post,
    PostComment,
    PostLike,
    User,
)

logger = logging.getLogger(__name__)

SOCIAL_ACTION_CEILING = 15
ENGAGEMENT_METRIC_TYPES = ("checkins", "participation", "social", "productivity")


async def compute_engagement_logs(ctx: dict[str, Any]) -> None:
    window_end = date.today() - timedelta(days=1)
    window_start = window_end - timedelta(days=6)
    prev_window_end = window_start - timedelta(days=1)
    prev_window_start = prev_window_end - timedelta(days=6)

    written = 0

    async with get_job_session() as session:
        users = await _load_users(session)
        if not users:
            logger.info("Engagement logs computed", extra={"window_end": str(window_end), "rows_written": 0})
            return

        checkin_counts = await _load_checkin_counts(session, window_start, window_end)
        activity_counts = await _load_activity_counts(session, window_start, window_end)
        social_counts = await _load_social_counts(session, window_start, window_end)
        completed_challenge_user_ids = await _load_completed_challenge_user_ids(session, window_start, window_end)
        previous_values = await _load_metric_values(session, prev_window_end)
        existing_rows = await _load_existing_rows(session, window_end)

        for user_id, organization_id, branch_id in users:
            checkin_days = checkin_counts.get(user_id, 0)
            activity_days = activity_counts.get(user_id, 0)
            social_actions = social_counts.get(user_id, 0)
            completed_challenge = user_id in completed_challenge_user_ids

            window_days = (window_end - window_start).days + 1
            checkins_score = cap_score(checkin_days, window_days)
            participation_score = cap_score(activity_days, window_days)
            social_score = cap_score(social_actions, SOCIAL_ACTION_CEILING)
            productivity_score = 0.5 * participation_score + 0.5 * (100.0 if completed_challenge else 0.0)

            scores = {
                "checkins": checkins_score,
                "participation": participation_score,
                "social": social_score,
                "productivity": productivity_score,
            }

            for metric_type, value in scores.items():
                previous_value = previous_values.get((user_id, metric_type))
                direction = trend_direction(value, previous_value)

                row = existing_rows.get((user_id, metric_type))
                if row is not None:
                    row.value = value
                    row.previous_value = previous_value
                    row.trend_direction = direction
                else:
                    session.add(EngagementLog(
                        organization_id=organization_id, branch_id=branch_id, user_id=user_id,
                        metric_type=metric_type, date=window_end, value=value,
                        previous_value=previous_value, trend_direction=direction,
                    ))
                written += 1

        await session.commit()

    logger.info("Engagement logs computed", extra={"window_end": str(window_end), "rows_written": written})


async def _load_users(session) -> list[tuple[str, str, str]]:
    result = await session.execute(select(User.id, User.organization_id, User.branch_id))
    return list(result.all())


async def _load_checkin_counts(session, window_start: date, window_end: date) -> dict[str, int]:
    result = await session.execute(
        select(DailyCheckin.user_id, func.count(func.distinct(DailyCheckin.checkin_date)))
        .where(DailyCheckin.checkin_date >= window_start, DailyCheckin.checkin_date <= window_end)
        .group_by(DailyCheckin.user_id)
    )
    return {user_id: count for user_id, count in result.all()}


async def _load_activity_counts(session, window_start: date, window_end: date) -> dict[str, int]:
    result = await session.execute(
        select(ActivityLog.user_id, func.count(func.distinct(ActivityLog.date)))
        .where(ActivityLog.date >= window_start, ActivityLog.date <= window_end)
        .group_by(ActivityLog.user_id)
    )
    return {user_id: count for user_id, count in result.all()}


async def _load_social_counts(session, window_start: date, window_end: date) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)

    posts_result = await session.execute(
        select(Post.user_id, func.count(Post.id))
        .where(func.date(Post.created_at) >= window_start, func.date(Post.created_at) <= window_end)
        .group_by(Post.user_id)
    )
    for user_id, count in posts_result.all():
        totals[user_id] += count

    comments_result = await session.execute(
        select(PostComment.user_id, func.count(PostComment.id))
        .where(func.date(PostComment.created_at) >= window_start, func.date(PostComment.created_at) <= window_end)
        .group_by(PostComment.user_id)
    )
    for user_id, count in comments_result.all():
        totals[user_id] += count

    likes_result = await session.execute(
        select(PostLike.user_id, func.count(PostLike.id))
        .where(func.date(PostLike.created_at) >= window_start, func.date(PostLike.created_at) <= window_end)
        .group_by(PostLike.user_id)
    )
    for user_id, count in likes_result.all():
        totals[user_id] += count

    joins_result = await session.execute(
        select(ClubMember.user_id, func.count(ClubMember.id))
        .where(func.date(ClubMember.joined_at) >= window_start, func.date(ClubMember.joined_at) <= window_end)
        .group_by(ClubMember.user_id)
    )
    for user_id, count in joins_result.all():
        totals[user_id] += count

    return dict(totals)


async def _load_completed_challenge_user_ids(session, window_start: date, window_end: date) -> set[str]:
    result = await session.execute(
        select(ChallengeParticipant.user_id)
        .where(
            ChallengeParticipant.completed_at.is_not(None),
            func.date(ChallengeParticipant.completed_at) >= window_start,
            func.date(ChallengeParticipant.completed_at) <= window_end,
        )
        .distinct()
    )
    return {row[0] for row in result.all()}


async def _load_metric_values(session, target_date: date) -> dict[tuple[str, str], float]:
    result = await session.execute(
        select(EngagementLog.user_id, EngagementLog.metric_type, EngagementLog.value)
        .where(EngagementLog.date == target_date, EngagementLog.metric_type.in_(ENGAGEMENT_METRIC_TYPES))
    )
    return {(user_id, metric_type): value for user_id, metric_type, value in result.all()}


async def _load_existing_rows(session, target_date: date) -> dict[tuple[str, str], EngagementLog]:
    result = await session.execute(
        select(EngagementLog).where(
            EngagementLog.date == target_date, EngagementLog.metric_type.in_(ENGAGEMENT_METRIC_TYPES)
        )
    )
    rows = result.scalars().all()
    return {(row.user_id, row.metric_type): row for row in rows}