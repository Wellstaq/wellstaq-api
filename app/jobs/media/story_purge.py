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

from sqlalchemy import delete, select

from app.core.database import get_db
from app.models import Story

logger = logging.getLogger(__name__)


async def purge_expired_stories(ctx: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    deleted_count = 0

    async for session in get_db():
        # TODO(s3): once S3 is wired up, fetch media_url for each
        # expiring row BEFORE deleting, and enqueue an object-deletion
        # job per URL — otherwise expired story media accumulates
        # forever in the bucket even after the DB row is gone.
        result = await session.execute(delete(Story).where(Story.expires_at <= now))
        deleted_count = result.rowcount
        await session.commit()
        break

    logger.info("Expired stories purged", extra={"deleted_count": deleted_count})