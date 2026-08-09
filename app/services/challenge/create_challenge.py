"""
app/services/challenge/create_challenge.py
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Challenge
from app.services.challenge._helpers import validate_org_and_branch
from app.services.storage import attach_media_asset, confirm_upload
from app.services.storage._helpers import extract_object_key

logger = logging.getLogger(__name__)


async def create_challenge(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str | None,
    wellbeing_challenge_id: str | None,
    name: str,
    description: str | None,
    image_url: str | None,
    start_date,
    end_date,
    metric_type: str | None,
    target_type: str,
    target_value,
    created_by: str,
) -> Challenge:
    await validate_org_and_branch(session=session, organization_id=organization_id, branch_id=branch_id)

    object_key = None
    if image_url is not None:
        object_key = extract_object_key(image_url)
        if not confirm_upload(object_key=object_key):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Image upload not found — upload may have failed or not completed yet.")

    normalized_metric_type = metric_type.strip().lower() if metric_type else None

    challenge = Challenge(
        organization_id=organization_id,
        branch_id=branch_id,
        wellbeing_challenge_id=wellbeing_challenge_id,
        name=name.strip(),
        description=description,
        image_url=image_url,
        start_date=start_date,
        end_date=end_date,
        status="upcoming",
        metric_type=normalized_metric_type,
        target_type=target_type,
        target_value=target_value,
        created_by=created_by,
    )
    session.add(challenge)
    await session.flush()
    await session.refresh(challenge)

    # Mark the uploaded image (if any) as attached so the pending-asset
    # sweep job leaves it alone. No-ops cleanly if object_key is None.
    await attach_media_asset(
        session=session, object_key=object_key,
        attached_to_type="challenge", attached_to_id=challenge.id,
    )

    logger.info(
        "Challenge created",
        extra={"challenge_id": challenge.id, "org_id": organization_id, "branch_id": branch_id, "target_type": target_type},
    )
    return challenge