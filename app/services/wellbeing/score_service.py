"""
app/services/wellbeing/score_service.py

Personal scores: full detail, self-only.
Aggregate scores: branch/org-level AVERAGE only, gated by
wellbeing.view_team, suppressed entirely below ANONYMITY_THRESHOLD
distinct participants — see _helpers.py.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, WellbeingScore
from app.core.constants import ANONYMITY_THRESHOLD


async def get_my_scores(*, session: AsyncSession, user_id: str, period: str | None = None) -> list[WellbeingScore]:
    query = select(WellbeingScore).where(WellbeingScore.user_id == user_id)
    if period is not None:
        query = query.where(WellbeingScore.period == period)
    result = await session.execute(query.order_by(WellbeingScore.period.desc()))
    return list(result.scalars().all())


async def get_branch_aggregate_score(
    *, session: AsyncSession, organization_id: str, branch_id: str, dimension_id: str, period: str
) -> tuple[float | None, int, bool]:
    """Returns (average_score, participant_count, suppressed). average_score
    is None whenever suppressed=True — never returns a real number below
    the anonymity threshold, regardless of caller permission."""
    result = await session.execute(
        select(func.avg(WellbeingScore.score), func.count(func.distinct(WellbeingScore.user_id)))
        .join(User, User.id == WellbeingScore.user_id)
        .where(
            WellbeingScore.organization_id == organization_id, WellbeingScore.dimension_id == dimension_id,
            WellbeingScore.period == period, User.branch_id == branch_id,
        )
    )
    avg_score, participant_count = result.first()
    participant_count = participant_count or 0

    if participant_count < ANONYMITY_THRESHOLD:
        return None, participant_count, True

    return float(avg_score), participant_count, False


async def get_org_aggregate_score(
    *, session: AsyncSession, organization_id: str, dimension_id: str, period: str
) -> tuple[float | None, int, bool]:
    result = await session.execute(
        select(func.avg(WellbeingScore.score), func.count(func.distinct(WellbeingScore.user_id)))
        .where(
            WellbeingScore.organization_id == organization_id, WellbeingScore.dimension_id == dimension_id,
            WellbeingScore.period == period,
        )
    )
    avg_score, participant_count = result.first()
    participant_count = participant_count or 0

    if participant_count < ANONYMITY_THRESHOLD:
        return None, participant_count, True

    return float(avg_score), participant_count, False