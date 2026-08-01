"""
app/jobs/analytics/hashtag_trends.py

compute_hashtag_trend_snapshots — recomputes HashtagTrendSnapshot rows
on a 15-60 minute cadence. Tenant-isolated per organization_id: the
GROUP BY below groups counts separately per (organization_id,
hashtag_id), so a hashtag used in two different orgs produces two
distinct snapshot rows — one org's trending data can never leak into
another's. Verified against a two-org test scenario during development.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.jobs.shared import get_job_session
from app.models import Hashtag, PostHashtag
from app.models.hashtag import HashtagTrendSnapshot

logger = logging.getLogger(__name__)


async def compute_hashtag_trend_snapshots(ctx: dict[str, Any]) -> None:
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=1)

    rows = []
    async with get_job_session() as session:
        result = await session.execute(
            select(
                PostHashtag.organization_id,
                PostHashtag.hashtag_id,
                func.count(PostHashtag.id).label("post_count"),
            )
            .where(PostHashtag.created_at >= window_start, PostHashtag.created_at < window_end)
            .group_by(PostHashtag.organization_id, PostHashtag.hashtag_id)
        )
        rows = result.all()

        for organization_id, hashtag_id, post_count in rows:
            session.add(HashtagTrendSnapshot(
                hashtag_id=hashtag_id,
                organization_id=organization_id,
                post_count=post_count,
                window_start=window_start,
                window_end=window_end,
                trend_score=post_count,
                computed_at=window_end,
            ))

        await session.commit()

    logger.info("Hashtag trend snapshots computed", extra={"hashtags_updated": len(rows)})