"""
app/services/engagement_log/get_engagement.py

Self-only reads — a user's own engagement trend. No branch/org
aggregate view built here; EngagementLog already carries branch_id/
organization_id for whenever a manager-facing aggregate is wanted, but
that's a separate, deliberate addition, not bundled in here by default.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engagement_log import EngagementLog

ENGAGEMENT_METRIC_TYPES = ("participation", "checkins", "social", "productivity")


async def get_engagement_trend(
    *, session: AsyncSession, user_id: str, metric_type: str, limit: int = 30
) -> list[tuple]:
    query = (
        select(EngagementLog.date, EngagementLog.value)
        .where(EngagementLog.user_id == user_id, EngagementLog.metric_type == metric_type)
        .order_by(EngagementLog.date.desc())
        .limit(limit)
    )
    rows = (await session.execute(query)).all()
    return sorted(rows, key=lambda r: r[0])


async def get_latest_snapshot(*, session: AsyncSession, user_id: str) -> dict:
    """One row per metric_type, most recent date each — used for the
    4-column 'current state' view rather than a full trend line."""
    result: dict = {"date": None, "checkins": None, "participation": None, "social": None, "productivity": None}

    for metric_type in ENGAGEMENT_METRIC_TYPES:
        query = (
            select(EngagementLog.date, EngagementLog.value)
            .where(EngagementLog.user_id == user_id, EngagementLog.metric_type == metric_type)
            .order_by(EngagementLog.date.desc())
            .limit(1)
        )
        row = (await session.execute(query)).first()
        if row is not None:
            result[metric_type] = row[1]
            if result["date"] is None:
                result["date"] = row[0]

    return result