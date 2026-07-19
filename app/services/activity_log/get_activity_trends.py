"""
app/services/activity_log/get_activity_trends.py
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog


async def get_activity_trend(
    *, session: AsyncSession, user_id: str, metric_type: str, granularity: str = "daily", limit: int = 30
) -> list[tuple]:
    """
    granularity: 'daily' (raw rows), 'weekly', or 'monthly' (grouped +
    summed). SQLite/Postgres both support strftime-free grouping via
    date_trunc-equivalent expressions differently, so this uses Python-
    side grouping for portability rather than dialect-specific SQL —
    acceptable at one user's data volume; revisit if this becomes a
    measured hot path across many users at once (e.g. an org-wide trend,
    which doesn't exist yet — this is the personal version only).
    """
    normalized_metric_type = metric_type.strip().lower()

    query = (
        select(ActivityLog.date, ActivityLog.value)
        .where(ActivityLog.user_id == user_id, ActivityLog.metric_type == normalized_metric_type)
        .order_by(ActivityLog.date.desc())
        .limit(limit if granularity == "daily" else limit * 31)  # widen the raw pull for aggregation windows
    )
    rows = (await session.execute(query)).all()

    if granularity == "daily":
        return sorted(rows, key=lambda r: r[0])

    from collections import defaultdict

    buckets: dict = defaultdict(lambda: 0)
    for entry_date, value in rows:
        if granularity == "weekly":
            key = entry_date - __import__("datetime").timedelta(days=entry_date.weekday())
        elif granularity == "monthly":
            key = entry_date.replace(day=1)
        else:
            raise ValueError(f"Invalid granularity '{granularity}'.")
        buckets[key] += value

    return sorted(buckets.items())