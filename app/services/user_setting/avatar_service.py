"""
app/services/user_settings/avatar_service.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.storage import attach_media_asset, confirm_upload, release_media_asset
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def update_avatar(*, session: AsyncSession, user_id: str, media_url: str) -> str:
    object_key = extract_object_key(media_url)
    if not confirm_upload(object_key=object_key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Media upload not found — upload may have failed or not completed yet.")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    old_avatar_url = user.avatar_url
    user.avatar_url = media_url
    await session.flush()

    # Mark the new avatar as attached so the pending-asset sweep leaves
    # it alone. attached_to_id is the user's own id — an avatar's
    # "parent" is the User row itself, not a separate media-holding row.
    await attach_media_asset(
        session=session, object_key=object_key,
        attached_to_type="avatar", attached_to_id=user.id,
    )

    # Release the old avatar object AFTER the new one is confirmed saved —
    # never delete before the DB write succeeds, so a failed update never
    # leaves the user with no avatar at all. release_media_asset looks up
    # the MediaAsset row by object_key directly, so it naturally no-ops
    # on anything that was never tracked (a default/shared placeholder
    # image, or a URL from before S3 was wired up) — no manual prefix
    # check needed.
    if old_avatar_url and old_avatar_url != media_url:
        try:
            await release_media_asset(session=session, object_key=extract_object_key(old_avatar_url))
        except Exception:
            # Non-fatal — the avatar update itself already succeeded;
            # a leftover orphaned S3 object is a minor cleanup miss,
            # not worth failing the whole request over.
            logger.error(
                "Failed to release old avatar media asset", exc_info=True,
                extra={"user_id": user_id},
            )

    logger.info("Avatar updated", extra={"user_id": user_id})
    return user.avatar_url