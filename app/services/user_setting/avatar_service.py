"""
app/services/user_settings/avatar_service.py
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import delete_object
from app.models import User
from app.services.storage import confirm_upload

logger = logging.getLogger(__name__)


async def update_avatar(*, session: AsyncSession, user_id: str, media_url: str) -> str:
    object_key = urlparse(media_url).path.lstrip("/")
    if not confirm_upload(object_key=object_key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Media upload not found — upload may have failed or not completed yet.")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    old_avatar_url = user.avatar_url
    user.avatar_url = media_url
    await session.flush()

    # Delete the old avatar object AFTER the new one is confirmed saved —
    # never delete before the DB write succeeds, so a failed update never
    # leaves the user with no avatar at all. Only deletes objects that
    # look like they're in our own "avatars/" S3 prefix (see
    # build_object_key's domain convention) — skips anything else (a
    # default/shared placeholder image, or a URL from before S3 was
    # wired up) so we never accidentally delete something we don't own.
    if old_avatar_url and old_avatar_url != media_url:
        old_object_key = urlparse(old_avatar_url).path.lstrip("/")
        if old_object_key.startswith("avatars/"):
            try:
                delete_object(object_key=old_object_key)
            except Exception:
                # Non-fatal — the avatar update itself already succeeded;
                # a leftover orphaned S3 object is a minor cleanup miss,
                # not worth failing the whole request over.
                logger.error(
                    "Failed to delete old avatar object", exc_info=True,
                    extra={"user_id": user_id, "old_object_key": old_object_key},
                )

    logger.info("Avatar updated", extra={"user_id": user_id})
    return user.avatar_url