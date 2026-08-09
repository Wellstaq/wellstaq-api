# app/jobs/media/asset_sweep.py
"""
app/jobs/media/asset_sweep.py

Two sweeps over media_assets, the safety net for the attach/release
calls in each domain's create/update/delete services:

  sweep_pending_media_assets — deletes objects that got a presigned
  URL but were never attached to anything within the grace window
  (abandoned uploads: user picked an image, then closed the modal,
  or hit a validation error and never retried).

  sweep_orphaned_media_assets — for rows marked 'attached', verifies
  the parent row still exists. Catches cases where a domain's
  delete/update path forgot to call release_media_asset (or a
  row was deleted via a path that bypassed the service layer,
  e.g. a manual DB fix).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.storage import delete_object
from app.jobs.shared import get_job_session
from app.models import MediaAsset, Post, Story, GroupChatMessage, User, Challenge, Event

logger = logging.getLogger(__name__)

PENDING_GRACE_PERIOD = timedelta(hours=48)

# Maps attached_to_type -> (model, id_column) so the orphan sweep can
# check existence generically instead of a big if/elif per domain.
_PARENT_LOOKUP: dict[str, tuple[Any, Any]] = {
    "post": (Post, Post.id),
    "story": (Story, Story.id),
    "chat_message": (GroupChatMessage, GroupChatMessage.id),
    "avatar": (User, User.id),
    "challenge": (Challenge, Challenge.id),
    "event": (Event, Event.id),
    "club": (Club, Club.id),
}

async def sweep_pending_media_assets(ctx: dict[str, Any]) -> None:
    cutoff = datetime.now(timezone.utc) - PENDING_GRACE_PERIOD
    deleted = 0

    async with get_job_session() as session:
        result = await session.execute(
            select(MediaAsset).where(MediaAsset.status == "pending", MediaAsset.created_at < cutoff)
        )
        for asset in result.scalars().all():
            try:
                delete_object(object_key=asset.object_key)
            except Exception:
                logger.warning("Failed to delete S3 object for pending asset", exc_info=True,
                                extra={"object_key": asset.object_key})
                continue
            asset.status = "deleted"
            deleted += 1

        await session.commit()

    logger.info("Pending media asset sweep complete", extra={"deleted": deleted})


async def sweep_orphaned_media_assets(ctx: dict[str, Any]) -> None:
    deleted = 0
    checked = 0

    async with get_job_session() as session:
        result = await session.execute(
            select(MediaAsset).where(MediaAsset.status == "attached")
        )
        assets = result.scalars().all()

        for asset in assets:
            checked += 1
            lookup = _PARENT_LOOKUP.get(asset.attached_to_type)
            if lookup is None:
                logger.warning("Unknown attached_to_type on media asset", extra={"type": asset.attached_to_type})
                continue

            model, id_column = lookup
            parent_exists = (await session.execute(
                select(id_column).where(id_column == asset.attached_to_id)
            )).scalar_one_or_none()

            if parent_exists is None:
                try:
                    delete_object(object_key=asset.object_key)
                except Exception:
                    logger.warning("Failed to delete S3 object for orphaned asset", exc_info=True,
                                    extra={"object_key": asset.object_key})
                    continue
                asset.status = "deleted"
                deleted += 1

        await session.commit()

    logger.info("Orphaned media asset sweep complete", extra={"checked": checked, "deleted": deleted})