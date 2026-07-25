"""
app/jobs/media/story_purge.py

purge_expired_stories — hard-deletes Story rows whose expires_at has
passed. Runs frequently (see worker.py cron) since stories are
ephemeral by design; reads already defensively filter expires_at, but
this job is what actually removes the rows (and eventually, once S3 is
wired up, the underlying media objects too — see TODO below).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, select

from app.core.database import get_db
from app.models import Story
from app.core.storage import delete_object

logger = logging.getLogger(__name__)


async def purge_expired_stories(ctx: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    deleted_count = 0

    async for session in get_db():
        expiring_stories = await session.execute(select(Story.media_url).where(Story.expires_at <= now))
        for (media_url,) in expiring_stories.all():
            object_key = urlparse(media_url).path.lstrip("/")
            try:
                delete_object(object_key=object_key)
            except Exception:
                logger.error("Failed to delete S3 object for expired story", exc_info=True, extra={"object_key": object_key})
        result = await session.execute(delete(Story).where(Story.expires_at <= now))
        deleted_count = result.rowcount
        await session.commit()
        break

    logger.info("Expired stories purged", extra={"deleted_count": deleted_count})