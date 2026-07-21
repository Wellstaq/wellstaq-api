"""
app/jobs/analytics/wellbeing_score.py

compute_wellbeing_scores — the only writer of WellbeingScore rows.
Computes a trailing-7-day score per user per dimension, once daily.

Dimension -> data source mapping (only dimensions with a real
underlying signal are computed):

  Mental:   derived from DailyCheckin.mood + stress_level, converted via
            CHECKIN_LEVEL_SCORES (good=100, tired=60, stressed=40,
            bad=20 — placeholder mapping, not clinically validated,
            revisit with real product/clinical input).
  Physical: derived from ActivityLog day-coverage over the window.
  Social:   posts + comments + likes + club joins, same signal shape as
            EngagementLog.social.

  TODO(financial-career-dimensions): Financial and Career dimensions
  have NO underlying data source anywhere in the current schema.
  Deliberately not computed rather than backed by a fabricated formula.

  TODO(averaging-formula): averaging every check-in in the window
  equally is a product decision, not a validated one — "latest check-in
  only" or a recency-weighted average are equally plausible alternatives.
  Revisit with real product input.

period is an ISO date string for the window's end date (e.g.
"2026-07-19"), representing "the 7-day window ending this date."

IDEMPOTENCY: existing rows for (user_id, dimension_id, period) are
bulk-loaded and updated in place.

REQUIRED DIMENSIONS: the job validates that Mental/Physical/Social all
exist in wellbeing_dimensions before computing anything for them
individually — if the seed script partially failed and one is missing,
that's logged as an error (visible, not silent) rather than the
dimension just quietly never getting scores written.

UNKNOWN VOCABULARY VALUES: if a DailyCheckin row has a mood/stress_level
value outside CHECKIN_LEVEL_SCORES (e.g. a future vocabulary addition
that didn't get mapped here), that check-in is logged and excluded from
the average — NOT silently treated as a score of 0, which would corrupt
the average toward a false low reading.

DEFERRED (tracked, not fixed here — same discipline as every other job
in this codebase): no batching/chunking of the write side, no
memory-bounding for very large user counts, loads every user
unconditionally rather than only users with some activity in the
window. All genuine concerns at real scale; premature at current scale.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.core.database import get_db
from app.jobs.helpers.scoring import cap_score
from app.models import ActivityLog, ClubMember, DailyCheckin, Post, PostComment, PostLike, User, WellbeingScore
from app.models.wellbeing import WellbeingDimension

logger = logging.getLogger(__name__)

CHECKIN_LEVEL_SCORES = {"good": 100, "tired": 60, "stressed": 40, "bad": 20}
SOCIAL_ACTION_CEILING = 15

REQUIRED_DIMENSIONS = ("Mental", "Physical", "Social")


async def compute_wellbeing_scores(ctx: dict[str, Any]) -> None:
    del ctx  # required by ARQ's call signature, unused here

    window_end = date.today() - timedelta(days=1)
    window_start = window_end - timedelta(days=6)
    period = window_end.isoformat()

    # Range comparisons on timestamp columns instead of func.date(...) —
    # preserves index usage on created_at, unlike wrapping the column in
    # a function.
    window_start_dt = datetime.combine(window_start, time.min, tzinfo=timezone.utc)
    window_end_dt = datetime.combine(window_end, time.max, tzinfo=timezone.utc)

    inserted = 0
    updated = 0

    async for session in get_db():
        try:
            dimension_ids = await _load_dimension_ids(session)
            missing = set(REQUIRED_DIMENSIONS) - dimension_ids.keys()
            if missing:
                logger.error(
                    "Required wellbeing dimensions missing from wellbeing_dimensions — "
                    "seed script may have failed. Affected dimensions will not be scored.",
                    extra={"missing_dimensions": sorted(missing)},
                )

            users = await _load_users(session)
            mental_scores = await _load_mental_scores(session, window_start, window_end)
            physical_scores = await _load_physical_scores(session, window_start_dt, window_end_dt)
            social_scores = await _load_social_scores(session, window_start_dt, window_end_dt)
            existing_rows = await _load_existing_rows(session, period)

            for user_id, organization_id in users:
                per_user_scores = {}
                if "Mental" in dimension_ids:
                    per_user_scores["Mental"] = mental_scores.get(user_id)
                if "Physical" in dimension_ids:
                    per_user_scores["Physical"] = physical_scores.get(user_id)
                if "Social" in dimension_ids:
                    per_user_scores["Social"] = social_scores.get(user_id)

                for dimension_name, score in per_user_scores.items():
                    if score is None:
                        continue

                    dimension_id = dimension_ids[dimension_name]
                    row = existing_rows.get((user_id, dimension_id))
                    if row is not None:
                        row.score = score
                        updated += 1
                    else:
                        session.add(WellbeingScore(
                            organization_id=organization_id, user_id=user_id, dimension_id=dimension_id,
                            score=score, period=period,
                        ))
                        inserted += 1

            await session.commit()
        except Exception:
            await session.rollback()
            raise
        break

    logger.info(
        "Wellbeing scores computed",
        extra={
            "period": period, "window_start": str(window_start), "window_end": str(window_end),
            "rows_inserted": inserted, "rows_updated": updated,
        },
    )


# ── Bulk loaders ─────────────────────────────────────────────────────────────

async def _load_dimension_ids(session) -> dict[str, str]:
    result = await session.execute(select(WellbeingDimension.name, WellbeingDimension.id))
    return {name: dim_id for name, dim_id in result.all()}


async def _load_users(session) -> list[tuple[str, str]]:
    result = await session.execute(select(User.id, User.organization_id))
    return list(result.all())


async def _load_mental_scores(session, window_start: date, window_end: date) -> dict[str, float]:
    """
    Averages each check-in's mood+stress_level scores across every
    check-in in the window, per user. Rows with an unrecognized
    mood/stress_level value (not in CHECKIN_LEVEL_SCORES) are logged and
    EXCLUDED from the average — never silently treated as 0, which
    would corrupt the average toward a false low reading.

    Done in Python after one bulk fetch — mapping a string vocabulary to
    numbers isn't cleanly expressible as a portable SQL CASE across both
    SQLite and Postgres without dialect branching.
    """
    result = await session.execute(
        select(DailyCheckin.user_id, DailyCheckin.mood, DailyCheckin.stress_level, DailyCheckin.checkin_date)
        .where(DailyCheckin.checkin_date >= window_start, DailyCheckin.checkin_date <= window_end)
    )
    rows = result.all()

    totals: dict[str, list[float]] = defaultdict(list)
    for user_id, mood, stress_level, checkin_date in rows:
        if mood not in CHECKIN_LEVEL_SCORES or stress_level not in CHECKIN_LEVEL_SCORES:
            logger.warning(
                "Unrecognized checkin mood/stress_level value — excluded from mental score average.",
                extra={"user_id": user_id, "checkin_date": str(checkin_date), "mood": mood, "stress_level": stress_level},
            )
            continue

        mood_score = CHECKIN_LEVEL_SCORES[mood]
        stress_score = CHECKIN_LEVEL_SCORES[stress_level]
        totals[user_id].append((mood_score + stress_score) / 2)

    return {user_id: sum(values) / len(values) for user_id, values in totals.items()}


async def _load_physical_scores(session, window_start_dt: datetime, window_end_dt: datetime) -> dict[str, float]:
    result = await session.execute(
        select(ActivityLog.user_id, func.count(func.distinct(ActivityLog.date)))
        .where(ActivityLog.date >= window_start_dt.date(), ActivityLog.date <= window_end_dt.date())
        .group_by(ActivityLog.user_id)
    )
    window_days = (window_end_dt.date() - window_start_dt.date()).days + 1
    return {user_id: cap_score(count, window_days) for user_id, count in result.all()}


async def _load_social_scores(session, window_start_dt: datetime, window_end_dt: datetime) -> dict[str, float]:
    """
    4 separate bulk queries (posts, comments, likes, club joins) — NOT
    a single bulk query, despite what an earlier version of this
    docstring implied. Each is still bulk in the sense of covering every
    user in one round trip, just 4 round trips total, not N.

    Uses timestamp range comparisons (>=, <) instead of func.date(...)
    to preserve index usage on the created_at/joined_at columns.
    """
    totals: dict[str, int] = defaultdict(int)

    for model, date_field in (
        (Post, Post.created_at), (PostComment, PostComment.created_at), (PostLike, PostLike.created_at),
    ):
        result = await session.execute(
            select(model.user_id, func.count(model.id))
            .where(date_field >= window_start_dt, date_field <= window_end_dt)
            .group_by(model.user_id)
        )
        for user_id, count in result.all():
            totals[user_id] += count

    joins_result = await session.execute(
        select(ClubMember.user_id, func.count(ClubMember.id))
        .where(ClubMember.joined_at >= window_start_dt, ClubMember.joined_at <= window_end_dt)
        .group_by(ClubMember.user_id)
    )
    for user_id, count in joins_result.all():
        totals[user_id] += count

    return {user_id: cap_score(count, SOCIAL_ACTION_CEILING) for user_id, count in totals.items()}


async def _load_existing_rows(session, period: str) -> dict[tuple[str, str], WellbeingScore]:
    result = await session.execute(select(WellbeingScore).where(WellbeingScore.period == period))
    rows = result.scalars().all()
    return {(row.user_id, row.dimension_id): row for row in rows}