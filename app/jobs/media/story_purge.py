"""
app/jobs/media/story_purge.py

purge_expired_stories — hard-deletes Story rows whose expires_at has
passed. Runs frequently (see worker.py cron) since stories are
ephemeral by design; reads already defensively filter expires_at, but
this job is what actually removes the rows AND releases their media
assets (so the orphan sweep doesn't have to — release_media_asset
also marks the MediaAsset row 'deleted', which raw delete_object()
calls never did).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.core.database import get_db
from app.models import Story
from app.services.storage import release_media_asset
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def purge_expired_stories(ctx: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    deleted_count = 0

    async for session in get_db():
        expiring_stories = await session.execute(
            select(Story.id, Story.media_url).where(Story.expires_at <= now)
        )
        rows = expiring_stories.all()

        for story_id, media_url in rows:
            if media_url:
                try:
                    await release_media_asset(session=session, object_key=extract_object_key(media_url))
                except Exception:
                    logger.error(
                        "Failed to release S3 object for expired story", exc_info=True,
                        extra={"story_id": story_id},
                    )

        result = await session.execute(delete(Story).where(Story.expires_at <= now))
        deleted_count = result.rowcount
        await session.commit()
        break

    logger.info("Expired stories purged", extra={"deleted_count": deleted_count})