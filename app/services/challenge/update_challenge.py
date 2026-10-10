"""
app/services/challenge/update_challenge.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CHALLENGE_STATUSES, TARGET_TYPES, TRACKING_METHODS
from app.services.challenge._helpers import get_challenge_or_404
from app.services.storage import attach_media_asset, confirm_upload, release_media_asset
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def edit_challenge(
    *,
    session: AsyncSession,
    organization_id: str,
    challenge_id: str,
    name: str | None,
    description: str | None,
    image_url: str | None,  # new
    start_date,
    end_date,
    metric_type: str | None,
    target_type: str | None,
    target_value,
    status_: str | None,
    tracking_method: str | None = None,
    activity_label: str | None = None,
    evidence_required: bool | None = None,
    requires_approval: bool | None = None,
):
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    if name is not None:
        challenge.name = name.strip()
    if description is not None:
        challenge.description = description

    if image_url is not None and image_url != challenge.image_url:
        object_key = extract_object_key(image_url)
        if not confirm_upload(object_key=object_key):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Image upload not found — upload may have failed or not completed yet.")

        old_image_url = challenge.image_url
        challenge.image_url = image_url

        # Mark the new upload as attached so the pending-sweep won't
        # reap it — without this it stays "pending" and gets deleted
        # by sweep_pending_media_assets after the grace period even
        # though it's the live challenge image.
        await attach_media_asset(
            session=session, object_key=object_key,
            attached_to_type="challenge", attached_to_id=challenge.id,
        )

        # Release the old image now that it's no longer referenced.
        # Without this, the old asset stays "attached" to a challenge
        # that still exists, so sweep_orphaned_media_assets can never
        # detect it as orphaned — it would sit in S3 forever.
        if old_image_url:
            try:
                await release_media_asset(session=session, object_key=extract_object_key(old_image_url))
            except Exception:
                logger.error(
                    "Failed to release old challenge image media asset", exc_info=True,
                    extra={"challenge_id": challenge_id},
                )

    if start_date is not None:
        challenge.start_date = start_date
    if end_date is not None:
        challenge.end_date = end_date
    if metric_type is not None:
        challenge.metric_type = metric_type.strip().lower()
    if target_type is not None:
        if target_type not in TARGET_TYPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid target_type '{target_type}'.")
        challenge.target_type = target_type
    if target_value is not None:
        challenge.target_value = target_value
    if status_ is not None:
        if status_ not in CHALLENGE_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid status '{status_}'.")
        challenge.status = status_
    if tracking_method is not None:
        if tracking_method not in TRACKING_METHODS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid tracking_method '{tracking_method}'.")
        challenge.tracking_method = tracking_method
    if activity_label is not None:
        challenge.activity_label = activity_label.strip()
    if evidence_required is not None:
        challenge.evidence_required = evidence_required
    if requires_approval is not None:
        challenge.requires_approval = requires_approval

    if challenge.target_type == "daily_minimum" and challenge.end_date is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "daily_minimum challenges require an end_date.")

    await session.flush()
    await session.refresh(challenge)
    logger.info("Challenge updated", extra={"challenge_id": challenge_id})
    return challenge


async def cancel_challenge(*, session: AsyncSession, organization_id: str, challenge_id: str):
    """Preferred over delete_challenge when the challenge has completions
    or in-progress participants worth keeping. Preserves all
    ChallengeParticipant/ChallengeProgressEntry rows — only the parent
    challenge's status changes."""
    challenge = await get_challenge_or_404(session=session, organization_id=organization_id, challenge_id=challenge_id)

    if challenge.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "Challenge is already cancelled.")

    challenge.status = "cancelled"
    await session.flush()
    await session.refresh(challenge)
    logger.info("Challenge cancelled", extra={"challenge_id": challenge_id})
    return challenge