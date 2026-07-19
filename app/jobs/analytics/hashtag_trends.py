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

from app.core.database import get_db
from app.models import Hashtag, PostHashtag
from app.models.hashtag import HashtagTrendSnapshot

logger = logging.getLogger(__name__)


async def compute_hashtag_trend_snapshots(ctx: dict[str, Any]) -> None:
    """
    Trend score formula (v1 — deliberately simple, revisit once real
    usage data exists to tune against):
        trend_score = posts_in_window

    This is intentionally just a raw count, not a decayed/weighted
    velocity score. A more sophisticated formula (e.g. comparing this
    window's count against the previous window's count to reward
    accelerating hashtags, not just currently-popular ones) is a
    reasonable v2 — flagging here rather than guessing at weights with
    no real traffic data to validate them against.

    Window: trailing 1 hour, ending at job run time. Chosen to be wider
    than the 15-minute cron cadence itself, so a hashtag with sparse but
    real activity doesn't flicker in and out of "trending" between
    consecutive runs just because it landed in a 15-minute gap.
    """
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=1)

    rows = []
    async for session in get_db():
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
                trend_score=post_count,  # v1 formula — see docstring
                computed_at=window_end,
            ))

        await session.commit()
        break

    logger.info("Hashtag trend snapshots computed", extra={"hashtags_updated": len(rows)})