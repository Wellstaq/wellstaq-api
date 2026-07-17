"""
app/services/challenge/create_challenge.py
"""

from __future__ import annotations

import logging

from app.models import Challenge
from app.services.challenge._helpers import validate_org_and_branch
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def create_challenge(
    *,
    session: AsyncSession,
    organization_id: str,
    branch_id: str | None,
    wellbeing_challenge_id: str | None,
    name: str,
    description: str | None,
    start_date,
    end_date,
    metric_type: str | None,
    target_type: str,
    target_value,
    created_by: str,
) -> Challenge:
    await validate_org_and_branch(session=session, organization_id=organization_id, branch_id=branch_id)

    # Normalized the same way Hashtag.tag_name is — metric_type is
    # freeform (no enum), but the frontend/mobile client will likely
    # branch logic off this string (e.g. "steps" -> show step counter).
    # Without normalization, "Steps" and "steps" silently become two
    # different values and that matching breaks.
    normalized_metric_type = metric_type.strip().lower() if metric_type else None

    # TODO(wellbeing-challenge-validation): validate wellbeing_challenge_id
    # exists in wellbeing_challenges if set — low risk, small global
    # lookup table.

    challenge = Challenge(
        organization_id=organization_id,
        branch_id=branch_id,
        wellbeing_challenge_id=wellbeing_challenge_id,
        name=name.strip(),
        description=description,
        start_date=start_date,
        end_date=end_date,
        status="draft",
        metric_type=normalized_metric_type,
        target_type=target_type,
        target_value=target_value,
        created_by=created_by,
    )
    session.add(challenge)
    await session.flush()
    await session.refresh(challenge)

    logger.info(
        "Challenge created",
        extra={"challenge_id": challenge.id, "org_id": organization_id, "branch_id": branch_id, "target_type": target_type},
    )
    return challenge