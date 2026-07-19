"""
app/services/wellbeing/org_challenge_service.py

Org-wide opt-in into wellbeing challenge categories — company-level
configuration, always org-scoped (never branch), gated by
org.settings.update per the permission seed.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationWellbeingChallenge
from app.models.wellbeing import WellbeingChallenge


async def opt_in_to_challenge(
    *, session: AsyncSession, organization_id: str, wellbeing_challenge_id: str
) -> OrganizationWellbeingChallenge:
    challenge_result = await session.execute(
        select(WellbeingChallenge).where(WellbeingChallenge.id == wellbeing_challenge_id)
    )
    if challenge_result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wellbeing challenge category not found.")

    existing = await session.execute(
        select(OrganizationWellbeingChallenge).where(
            OrganizationWellbeingChallenge.organization_id == organization_id,
            OrganizationWellbeingChallenge.wellbeing_challenge_id == wellbeing_challenge_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Organization has already opted into this challenge category.")

    opt_in = OrganizationWellbeingChallenge(organization_id=organization_id, wellbeing_challenge_id=wellbeing_challenge_id)
    session.add(opt_in)
    await session.flush()
    await session.refresh(opt_in)
    return opt_in


async def opt_out_of_challenge(*, session: AsyncSession, organization_id: str, wellbeing_challenge_id: str) -> None:
    result = await session.execute(
        select(OrganizationWellbeingChallenge).where(
            OrganizationWellbeingChallenge.organization_id == organization_id,
            OrganizationWellbeingChallenge.wellbeing_challenge_id == wellbeing_challenge_id,
        )
    )
    opt_in = result.scalar_one_or_none()
    if opt_in is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization has not opted into this challenge category.")

    await session.delete(opt_in)
    await session.flush()


async def list_opted_in_challenges(*, session: AsyncSession, organization_id: str) -> list[OrganizationWellbeingChallenge]:
    result = await session.execute(
        select(OrganizationWellbeingChallenge).where(OrganizationWellbeingChallenge.organization_id == organization_id)
    )
    return list(result.scalars().all())